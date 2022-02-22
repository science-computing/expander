#!/home/michael/karton-venv/bin/python3

#import datetime
import json
import sys

import karton.core

EXTRACTOR_REPORTS = "extractor.reports"
EXTRACTOR_JOB_CACHE = "extractor.cache:"

config = karton.core.Config(sys.argv[1])


class PeekabooCacheResponder(karton.core.Karton):
    identity = "extractor.cache-responder"
    filters = [
        {
            "type": "extractor-sample",
        }
    ]

    def process(self, task: karton.core.Task) -> None:
        sample = task.get_resource("sample")
        file_name = sample.name
        sha256 = sample.sha256
        content_type = task.get_payload("content-type")
        content_disposition = task.get_payload("content-disposition")

        # look for an earlier job that processed the same sample with the same
        # parameters, sorted set provides implicit sorting from oldes to newest
        # extractor.cache:<sha256>[<job_id>] = <hours since epoch (float)>
        cache_key = EXTRACTOR_JOB_CACHE + sha256
        for job_id in self.backend.redis.zrange(cache_key, 0, -1, desc=True):
            report_json = self.backend.redis.hget(EXTRACTOR_REPORTS, job_id)
            if report_json is None:
                self.log.warning(
                    "%s: Cache inconsistency, report %s missing",
                    task.root_uid, job_id)
                continue

            report = json.loads(report_json)

            # schema checking

            # look for root sample
            report = report.get("report")
            if report is None:
                self.log.debug(
                    "%s: Cached job %s has no per-job report",
                    task.root_uid, job_id)
                continue

            root_sample = None
            if report.get("worst-sample", {}).get("root-sample") is True:
                root_sample = report.get("worst-sample")
            if root_sample is None:
                for job in report.get("jobs", []):
                    if job.get("root-sample") is True:
                        root_sample = job

            if root_sample is None:
                self.log.debug(
                    "%s: Cached job %s has no root sample",
                    task.root_uid, job_id)
                continue

            if (root_sample.get("sha256") != sha256 or
                    root_sample.get(
                        "content-disposition") != content_disposition or
                    root_sample.get("content-type") != content_type or
                    root_sample.get("file-name") != file_name):
                self.log.debug(
                    "%s: Cached job %s had different parameters for root "
                    "sample", task.root_uid, job_id)
                continue

            # update cache and be done
            self.backend.redis.hset(
                EXTRACTOR_REPORTS, task.root_uid, report_json)
            self.log.info(
                "%s: Short-circuited with cached report %s",
                task.root_uid, job_id)

            # optionally add to cache itself - does that make sense?
            # that makes it pretend to be the most recent report although it's
            # a copy of a cached one
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
        self.log.info("%s: No cache match - passed on", task.root_uid)


if __name__ == "__main__":
    c = PeekabooCacheResponder(config)
    c.loop()
