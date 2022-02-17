#!/home/michael/karton-venv/bin/python3

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
        if task.headers.get("kind") == "archive":
            self.log.info(
                "%s: Ignoring archive %s", task.root_uid, sample.name)
            return

        file_name = sample.name
        content_type = task.get_payload("content-type")
        content_disposition = task.get_payload("content-disposition")

        # submitted ;)
        peekaboo_job_id = str(uuid.uuid4())

        # submit a first tracker poker task
        poker_task = karton.core.Task(
            headers={
                "type": "peekaboo-job",
                "state": "new",
            },
            payload_persistent={
                "peekaboo-job-id": str(peekaboo_job_id),
            })

        # add metadata if we have it and make it persistent
        if file_name is not None:
            poker_task.add_payload("file-name", file_name, persistent=True)
        if content_type is not None:
            poker_task.add_payload(
                "content-type", content_type, persistent=True)
        if content_disposition is not None:
            poker_task.add_payload(
                "content-disposition", content_disposition, persistent=True)

        self.send_task(poker_task)

        self.log.info(
            "%s:%s: Submitted with file-name %s, content-type %s and "
            "content-dispostion %s (%s)", task.root_uid, peekaboo_job_id,
            file_name, content_type, content_disposition, poker_task.uid)

        # do not do lengthy processing down here because it aggravates a race
        # condition with the all-jobs-finished check in the poker


if __name__ == "__main__":
    c = PeekabooSubmitter(config)
    c.loop()
