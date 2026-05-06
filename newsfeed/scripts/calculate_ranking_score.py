from celery import shared_task
from django.core.cache import cache
from ..helpers.query_functions import update_ranking_score


@shared_task
def calculate_ranking_score_task(post_id, update_type, is_decrease):
    print("Init Score Recalculation...")

    lock_key = f"active_ranking_update_{post_id}"

    if not cache.add(lock_key, "locked", timeout=10):
        print(f"Post {post_id} update already in progress. Skipping.")
        return

    try:
        print(f"Recalculating score for post {post_id}...")
        update_ranking_score(post_id, update_type, is_decrease)
    finally:
        pass
