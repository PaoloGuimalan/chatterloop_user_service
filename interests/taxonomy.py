"""
The seed interest taxonomy.

Data, not logic - kept in the app rather than in scripts/ so anything else
(an admin screen, a suggestion endpoint) can import it. The loader that applies
it is interests/scripts/seed_taxonomy.py.

WHY THIS EXISTS
---------------
Interest rows were only ever created as a side effect of diary tagging, so the
vocabulary grew into 63 flat, idiosyncratic terms with no hierarchy - "dbeaver",
"orcr", "firstentry" beside "basketball" and "docker". That makes
resolve_interest()'s ancestor walk a no-op (nothing has a parent) and caps what
content tagging can ever recognise, since the moderation service can only match
against terms that already exist.

TWO LEVELS, DELIBERATELY
------------------------
CATEGORY -> interest. MAX_INTEREST_DEPTH allows five, but the model's own
docstring says the tree is "meant to stay shallow", and every extra level makes
the ancestor walk longer for no gain in practice.

TERMS ARE CHOSEN TO BE UNAMBIGUOUS IN PROSE
-------------------------------------------
The moderation service matches these against post text with word boundaries, so
a generic term becomes a false positive generator - and this user base is
developer-heavy, where "running" means running a script, "history" means git
history, and "space" means disk space. Hence "distance running", "world
history", "space exploration". Fixing that here, in the data, is better than
adding a stoplist mechanism to the matcher.
"""

# CATEGORY -> child interests. Category names become root Interest rows.
SEED = {
    "Technology": [
        "software engineering",
        "programming",
        "web development",
        "mobile development",
        "devops",
        "cloud computing",
        "databases",
        "artificial intelligence",
        "machine learning",
        "cybersecurity",
        "open source",
        "data science",
        "gadgets",
    ],
    "Gaming": [
        "pc gaming",
        "console gaming",
        "mobile gaming",
        "esports",
        "game development",
        "board games",
        "tabletop rpg",
    ],
    "Music": [
        "live music",
        "music production",
        "concerts",
        "hip hop",
        "rock music",
        "pop music",
        "jazz",
        "classical music",
        "k-pop",
        # Original Pilipino Music - the local scene is a real interest here and
        # would never appear in an imported generic taxonomy.
        "opm",
    ],
    "Film and TV": [
        "movies",
        "tv series",
        "anime",
        "documentaries",
        "film making",
    ],
    "Sports": [
        "basketball",
        "football",
        "volleyball",
        "tennis",
        "badminton",
        "boxing",
        "mixed martial arts",
        "distance running",
        "cycling",
        "swimming",
        "motorsports",
    ],
    "Food and Drink": [
        "cooking",
        "baking",
        "coffee",
        "restaurants",
        "street food",
        "desserts",
        "sushi",
        "barbecue",
        "vegan food",
        "cocktails",
    ],
    "Travel": [
        "road trips",
        "backpacking",
        "beaches",
        "hiking",
        "camping",
        "city breaks",
        "island hopping",
    ],
    "Health and Fitness": [
        "gym",
        "weight training",
        "yoga",
        "mental health",
        "nutrition",
        "meditation",
        "sleep health",
    ],
    "Arts and Design": [
        "graphic design",
        "illustration",
        "ui design",
        "painting",
        "digital art",
        "architecture",
        "arts and crafts",
    ],
    "Photography": [
        "portrait photography",
        "street photography",
        "landscape photography",
        "film photography",
        "drone photography",
    ],
    "Business and Finance": [
        "entrepreneurship",
        "investing",
        "stock market",
        "cryptocurrency",
        "personal finance",
        "marketing",
        "real estate",
    ],
    "Science": [
        "space exploration",
        "astronomy",
        "biology",
        "physics",
        "climate change",
        "environmental issues",
    ],
    "Education": [
        "studying",
        "online courses",
        "language learning",
        "career development",
    ],
    "Lifestyle": [
        "fashion",
        "beauty",
        "skincare",
        "home decor",
        "minimalism",
        "thrifting",
    ],
    "Pets and Animals": [
        "dogs",
        "cats",
        "aquarium",
        "birds",
        "pet care",
    ],
    "Relationships and Family": [
        "friendship",
        "dating",
        "parenting",
        "weddings",
        "family life",
    ],
    "Vehicles": [
        "cars",
        "motorcycles",
        "car modification",
        "road safety",
        "public transport",
    ],
    "Books and Writing": [
        "fiction writing",
        "non fiction",
        "poetry",
        "journaling",
        "book reviews",
    ],
    "Nature and Outdoors": [
        "gardening",
        "mountaineering",
        "wildlife",
        "surfing",
        "scuba diving",
    ],
    "News and Culture": [
        "current events",
        "politics",
        "world history",
        "philosophy",
        "religion",
    ],
}


# Existing rows that are real interests but whose exact wording is not in SEED.
# Names already present in SEED are deliberately absent here - SEED parents them
# on its own pass, and listing them twice reported the same adoption twice.
# Adopting them costs nothing and preserves everything: every one of these
# already carries EntityInterestAffinity and diary EntryTagLink rows, so setting
# a parent is strictly additive - no data moves, nothing is renamed.
REPARENT = {
    # Developer tooling and the platform's own stack.
    "AI": "Technology",
    "cassandradb": "Technology",
    "database": "Technology",
    "dbeaver": "Technology",
    "dev": "Technology",
    "digitalocean": "Technology",
    "docker": "Technology",
    "docker swarm": "Technology",
    "draganddrop": "Technology",
    "kubernetes": "Technology",
    "nosql": "Technology",
    "s3": "Technology",
    "schema": "Technology",
    "techdebt": "Technology",
    "production": "Technology",
    "release": "Technology",
    "spaces": "Technology",
    "chatterloop": "Technology",
    "chatterloop diary": "Technology",
    "startup": "Business and Finance",
    "shark tank": "Business and Finance",
    "opportunity": "Business and Finance",
    "alternative rock": "Music",
    "linkinpark": "Music",
    # NOTE: the pre-existing lowercase "sports" row is deliberately NOT listed.
    # It normalises to the same key as the "Sports" category, so
    # get_or_create_by_name() adopts that very row AS the category - keeping its
    # affinity and diary history - and listing it here would only ask it to
    # parent itself.
    "bike ride": "Sports",
    "casino": "Lifestyle",
    "pet": "Pets and Animals",
    "girlfriend": "Relationships and Family",
    "partner": "Relationships and Family",
    "relationships": "Relationships and Family",
    "love": "Relationships and Family",
    "sleepless": "Health and Fitness",
    "stress": "Health and Fitness",
    "motorcycle": "Vehicles",
    # Philippine road, toll and vehicle-registration vocabulary. Genuinely
    # driving-related, and exactly the kind of local term a generic taxonomy
    # would never contain.
    "lto": "Vehicles",
    "orcr": "Vehicles",
    "RFID": "Vehicles",
    "datmr": "Vehicles",
    "NLEX": "Vehicles",
    "SLEX": "Vehicles",
    "Express way": "Vehicles",
    "PH Road": "Vehicles",
    "north edsa": "Vehicles",
    "bgc": "Travel",
    "solaire north": "Travel",
}


# Existing rows deliberately LEFT as orphan roots. They are not interests -
# they are test data, UI nouns, or transient moods - but every one carries
# affinity and diary rows, so deleting them would destroy real user history for
# no benefit. Listed explicitly so their absence from REPARENT reads as a
# decision rather than an oversight.
LEAVE_ORPHANED = {
    "attachments",
    "boredom",
    "firstentry",
    "kick off",
    "lol",
    "Lorem",
    "new entry",
    "test",
    "test entry",
    "ubiquity",
    "weekend",
}


def counts():
    return {
        "categories": len(SEED),
        "seed_interests": sum(len(children) for children in SEED.values()),
        "reparent": len(REPARENT),
        "left_orphaned": len(LEAVE_ORPHANED),
    }
