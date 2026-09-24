-- Moments & Thoughts, phase 1 - Cassandra (Astra) schema changes.
--
-- RUN BY HAND, BEFORE deploying any service that declares these columns.
-- Nothing in the deploy runs sync_cassandra, and cqlengine names every column
-- of a model in its SELECTs, so a user_service build whose NewsfeedIndex /
-- TrendingPool models carry `kind` fails every feed read against a table that
-- does not have the column yet.
--
-- Replace `chatterloop` with the value of CASSANDRA_DB_KEYSPACE if it differs.
-- CQL has no ADD COLUMN IF NOT EXISTS: on a re-run the two ALTERs fail with
-- "column already exists", which is harmless; the index uses IF NOT EXISTS.

-- 1. The post's KIND (feed | moment | thought) on the fan-out inbox.
--
--    Deliberately not `type`: that column already exists and holds the REASON
--    the row is in the feed (fanout | comment | ...). Rows written before this
--    column existed read back as null and are treated as "feed"; they age out
--    on the table's 14-day TTL.
ALTER TABLE chatterloop.newsfeed_index ADD kind text;

-- 2. Same column on the trending pool. Nothing writes this table today (it is
--    only read, by newsfeed/helpers/query_functions.py fetch_trending_posts),
--    so whatever populates it later must set `kind` too.
ALTER TABLE chatterloop.trending_pool ADD kind text;

-- 3. "Who viewed this moment". user_engagement_log is partitioned by the
--    VIEWER (user_id = viewer entity id), so looking a target up by id is a
--    full-cluster scan without this. Storage-Attached Index, Astra's native
--    secondary index; it builds in the background on existing rows.
CREATE CUSTOM INDEX IF NOT EXISTS user_engagement_log_target_id_idx
  ON chatterloop.user_engagement_log (target_id)
  USING 'StorageAttachedIndex';
