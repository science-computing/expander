#!/home/michael/karton-venv/bin/python3

import random
import sys
import uuid

import karton.core
import karton.core.backend

config = karton.core.Config(sys.argv[1])

class PeekabooSubmitter(karton.core.Karton):
    identity = "extractor.peekaboo-submitter"
    filters = [
        {
            "type": "sample",
            "stage": "recognized"
        },
        {
            "type": "sample",
            "stage": "unrecognized"
        }
    ]

    def process(self, task: karton.core.Task) -> None:
        sample = task.get_resource("sample")
        file_name = sample.name
        content_type = task.get_payload("content-type")
        content_disposition = task.get_payload("content-disposition")

        # submit a first tracker poker task
        poker_task = karton.core.Task(
            headers={
                "type": "peekaboo-job",
                "state": "new",
            })

        sample_kind = task.headers.get("kind")
        if sample_kind == "archive":
            self.log.info(
                "%s: Ignoring archive %s", task.root_uid, file_name)

            # no job ID payload but additional status to distinguish
            # reasons for missing job ID, job-state vs. task state(!)
            poker_task.add_payload("job-state", "archive-ignored")
        else:
            if random.randint(0, 10):
                # submitted ;)
                peekaboo_job_id = str(uuid.uuid4())

                self.log.info(
                    "%s:%s: Submitted with file-name %s, content-type %s and "
                    "content-dispostion %s", task.root_uid, peekaboo_job_id,
                    file_name, content_type, content_disposition)

                poker_task.add_payload("job-state", "submitted")
                poker_task.add_payload(
                    "peekaboo-job-id", peekaboo_job_id, persistent=True)
            else:
                failure_reason = [
                    "host unreachable",
                    "connection refused",
                    "internal server error"][random.randint(0, 2)]

                self.log.info(
                    "%s: Submit of file with name %s, content-type %s and "
                    "content-dispostion %s failed: %s", task.root_uid,
                    file_name, content_type, content_disposition,
                    failure_reason)

                poker_task.add_payload("job-state", "submit-failed")
                poker_task.add_payload(
                    "failure-reason", failure_reason, persistent=True)

        # add metadata if we have it and make it persistent
        if file_name is not None:
            poker_task.add_payload("file-name", file_name, persistent=True)
        if content_type is not None:
            poker_task.add_payload(
                "content-type", content_type, persistent=True)
        if content_disposition is not None:
            poker_task.add_payload(
                "content-disposition", content_disposition, persistent=True)

        extraction_level = task.get_payload("extraction_level")
        parent = task.get_payload("parent")
        if extraction_level is not None and parent is not None:
            poker_task.add_payload(
                "extraction-level", extraction_level, persistent=True)
            poker_task.add_payload(
                "extracted-from", parent.name, persistent=True)

        self.send_task(poker_task)

        # do not do lengthy processing down here because it aggravates a race
        # condition with the all-jobs-finished check in the poker


if __name__ == "__main__":
    c = PeekabooSubmitter(config)
    c.loop()
