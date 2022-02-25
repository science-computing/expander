#!/home/michael/karton-venv/bin/python3

import datetime
import hashlib
import json
import sys

import karton.core

USE_DEDUPER = True

EXTRACTOR_REPORTS = "extractor.reports"
EXTRACTOR_JOB_CACHE = "extractor.cache:"

CACHE_AGE_OUT_INTERVAL = datetime.timedelta(seconds=60)
CACHE_MAX_AGE = datetime.timedelta(seconds=240)

config = karton.core.Config(sys.argv[1])


class PeekabooCacheResponder(karton.core.Karton):
    identity = "extractor.cache-responder"

    def __init__(self, config=None, identity=None, backend=None):
        self.filters = [
            {
                "type": "extractor-sample",
                "state": "new",
            }
        ]

        if USE_DEDUPER:
            self.filters = [
                {
                    "type": "extractor-sample",
                    "state": "deduped",
                }
            ]

        super().__init__(config=config, identity=identity, backend=backend)

        # do gc on first task
        self.last_age_out = datetime.datetime(
            1970, 1, 1, tzinfo=datetime.timezone.utc)

        # come to think of it, do gc now
        self.age_out()

    def age_out(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        if self.last_age_out + CACHE_AGE_OUT_INTERVAL > now:
            return

        cutoff_date = datetime.datetime.now(
            datetime.timezone.utc) - CACHE_MAX_AGE
        cutoff_score = cutoff_date.timestamp() / 3600
        for entry in self.backend.redis.keys(EXTRACTOR_JOB_CACHE + "*"):
            # keys becoming empty makes them magically disappear as well
            aged_out = self.backend.redis.zremrangebyscore(
                entry, 0, cutoff_score)
            if aged_out:
                self.log.info(
                    "Removed %d old entries from cache entry %s",
                    aged_out, entry)

        self.last_age_out = now

    def process(self, task: karton.core.Task) -> None:
        # aging out old cache entries not only saves space but causes
        # duplicated, fresh analysis after cache became empty - may be desired
        self.age_out()

        sample = task.get_resource("sample")

        # file name can be None in resource
        file_name = sample.name
        if file_name is None:
            file_name = ""

        sha256 = sample.sha256
        if sha256 is None:
            # basically impossible but not much code
            sha256 = hashlib.sha256(sample.content).hexdigest()

        criteria_hash = hashlib.sha256(sha256.encode("ascii"))
        criteria_hash.update(
            task.get_payload("content-disposition", "").encode("utf-8"))
        criteria_hash.update(
            task.get_payload("content-type", "").encode("utf-8"))
        criteria_hash.update(file_name.encode("utf-8"))
        criteria_key = criteria_hash.hexdigest()

        # look for an earlier job that processed the same sample with the same
        # parameters, sorted set provides implicit sorting from oldes to newest
        # extractor.cache:<sha256>[<job_id>] = <hours since epoch (float)>
        cache_key = EXTRACTOR_JOB_CACHE + criteria_key
        for job_id in self.backend.redis.zrange(cache_key, 0, -1, desc=True):
            report_json = self.backend.redis.hget(EXTRACTOR_REPORTS, job_id)
            if report_json is None:
                self.log.warning(
                    "%s: Cache inconsistency, report %s missing",
                    task.root_uid, job_id)
                continue

            report = json.loads(report_json)

            # schema checking

            # update cache and be done
            self.backend.redis.hset(
                EXTRACTOR_REPORTS, task.root_uid, json.dumps(report))
            self.log.info(
                "%s: Short-circuited with cached report %s",
                task.root_uid, job_id)

            # optionally add to cache itself - does that make sense?
            # that makes it pretend to be the most recent report although it's
            # a copy of a cached one - would prevent aging-out (see above),
            # i.e. keep frequently-used cache entries "hot"
            #now = datetime.datetime.now(
            #    datetime.timezone.utc).timestamp() / 3600
            #self.backend.redis.zadd(cache_key, {task.root_uid: now})
            return

        # pass on the unmodified payload but now addressed to the classifier
        classifier_task = task.derive_task({
            "type": "sample",
            "kind": "raw",
        })
        self.send_task(classifier_task)
        self.log.info(
            "%s: No cache match on %s - passed on (%s)",
            task.root_uid, criteria_key, task.uid)


if __name__ == "__main__":
    c = PeekabooCacheResponder(config)
    c.loop()
