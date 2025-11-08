from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Emoji, Post, PreviewCount
import uuid


@receiver(post_save, sender=Emoji)
def create_post_preview_counts(sender, instance, created, **kwargs):
    if created:
        # For each existing post, create a PreviewCount with count=0 for the new emoji
        posts = Post.objects.all()
        post_preview_counts = [
            PreviewCount(
                preview_id=str(uuid.uuid4()), post=post, emoji=instance, count=0
            )
            for post in posts
        ]
        PreviewCount.objects.bulk_create(post_preview_counts)


@receiver(post_save, sender=Post)
def create_preview_counts_for_new_post(sender, instance, created, **kwargs):
    if created:
        emojis = Emoji.objects.all()
        preview_counts = [
            PreviewCount(
                preview_id=str(uuid.uuid4()), post=instance, emoji=emoji, count=0
            )
            for emoji in emojis
        ]
        PreviewCount.objects.bulk_create(preview_counts)
