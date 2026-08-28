"""
Hashtags written into a post caption or a comment, turned into interests.

WHY THIS RUNS AT CREATION TIME
------------------------------
A hashtag is the one tag that needs no model to read. The author typed it
outright, which makes it the highest-confidence signal the platform has, and
extracting it is a regular expression rather than a classifier.

The moderation service already promotes hashtags to interests, but only when it
is ONLINE - the publishers are gated on a Redis presence key and skip silently
when it is absent, leaving the work to that service's next database scour. That
is the right trade for captioning an image; it is the wrong one for a hashtag,
which is why this path exists alongside it rather than instead of it. The
author sees their tag registered when they press post.

WHAT THIS DELIBERATELY DOES NOT DO
----------------------------------
It does not touch EntityInterestAffinity or InterestTrendingScore.

Those are SCORES, and scores double-count when two writers both believe they
own them. The moderation sink is the single writer for both, and it reaches the
same post moments later by queue or hours later by scour - either way exactly
once. This path writes only the things that are idempotent by construction: an
interest row keyed on a unique normalized_name, and a link row under a unique
(post, interest) constraint. Running both paths over the same post therefore
converges rather than inflating.

FOUR IMPLEMENTATIONS HAVE TO AGREE
----------------------------------
Same hazard mentions already documents, one participant worse:

  - moderation_service/core/vocabulary.py   hashtags()      <- canonical
  - server/reusables/hooks/hashtags.js      extractHashtags()
  - webapp/src/reusables/hooks/hashtags.ts  extractHashtags()
  - this file

They must agree on the pattern, on the separator rule, and on the two name
forms. Disagreement does not raise; it quietly creates a second interest for a
tag that already exists, which is exactly the duplication the normalized key
was introduced to prevent.
"""

import logging
import re

from interests.models import Interest, PostInterestLink, display_name, normalize_key

logger = logging.getLogger(__name__)

# The lookbehind excludes an HTML numeric entity: this platform stores authored
# text escaped, so "didn&#039;t" contains "#039" and matched a bare "#\w+"
# pattern well enough to be tagged as a declared interest. "&" before a "#"
# means punctuation, never a tag. (?<!\w) additionally stops a "#" mid-token
# ("a#b") and, usefully, a URL fragment - "example.com/page#section" is an
# address, not something anybody tagged.
HASHTAG_PATTERN = re.compile(r"(?<![&\w])#([\w-]{2,50})")

# Hyphens and underscores inside a hashtag are word separators: "#north-edsa"
# and "#docker_swarm" mean the multi-word interests they obviously mean.
# Applied ONLY here - doing it during normalisation would corrupt a
# legitimately hyphenated interest name such as "e-commerce".
SEPARATOR_RUN = re.compile(r"[-_]+")


def extract_hashtags(text):
    """Readable interest names for every hashtag in `text`, in order, deduped.

    "#north-edsa" gives "north edsa" - the readable form, not the squashed key.
    Both identify the same interest because normalize_key() removes the spaces
    again, but only one of them is fit to display when the hashtag is new and
    becomes a row somebody later reads in the UI.
    """
    if not text:
        return []

    seen = set()
    names = []

    for raw in HASHTAG_PATTERN.findall(text):
        # At least one letter. "#2024" is a year and "#1" is a rank; neither is
        # an interest, and the taxonomy should not grow one.
        if not any(char.isalpha() for char in raw):
            continue

        readable = display_name(SEPARATOR_RUN.sub(" ", raw))
        key = normalize_key(readable)
        if key and key not in seen:
            seen.add(key)
            names.append(readable)

    return names


def resolve_hashtag_interests(text):
    """Interest rows for the hashtags in `text`, creating any that are new.

    Creation is the point: a hashtag nobody has used before is still a real
    declaration of what the content is about. get_or_create_by_name is what
    decides whether it already exists, keyed on the normalized name, so
    "#NewsAndCulture" finds the existing "News and Culture" instead of
    founding a rival spelling of it.
    """
    interests = []

    for name in extract_hashtags(text):
        try:
            interest, _ = Interest.objects.get_or_create_by_name(name)
        except Exception:
            # One malformed tag must not cost the caller their post. The tag is
            # lost, not the content - and the moderation service's own pass
            # over the same text will try again independently.
            logger.exception("hashtags: could not resolve %r", name)
            continue
        interests.append(interest)

    return interests


def save_post_hashtags(post, text=None):
    """Link every hashtag in a post's caption to the post.

    Returns the interests linked. `text` defaults to the post's own caption;
    pass it explicitly when the caption is not yet saved on the instance.
    """
    caption = text if text is not None else (post.caption or "")
    interests = resolve_hashtag_interests(caption)
    if not interests:
        return []

    PostInterestLink.objects.bulk_create(
        [
            PostInterestLink(
                post=post,
                interest=interest,
                source=PostInterestLink.SOURCE_HASHTAG,
                # Null, like a manual pick. A hashtag is not a guess that could
                # have been wrong, so there is no confidence to record.
                confidence=None,
            )
            for interest in interests
        ],
        # The unique (post, interest) constraint is the idempotency: the
        # moderation service links the same pairs from its own pass, and
        # whichever arrives second must be a no-op rather than an error.
        ignore_conflicts=True,
    )
    return interests


def save_comment_hashtags(comment):
    """Link every hashtag in a comment to the comment's PARENT POST.

    There is no comment-to-interest table, and adding one to hold this would be
    a schema for a link nothing reads. The post is what the interest graph
    ranks and what the topic drill-down lists, and a comment tagging a topic is
    a genuine signal that the post belongs to it - which is the same call
    the moderation service's interest sink already makes for comments.
    """
    post = getattr(comment, "post", None)
    if post is None:
        return []

    return save_post_hashtags(post, text=comment.text or "")
