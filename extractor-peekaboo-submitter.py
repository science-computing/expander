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
            print("Ignoring archive %s", sample.name)
            return

        content_type = task.payload.get('content-type')
        content_disposition = task.payload.get('content-disposition')

        # submitted ;)
        peekaboo_job_id = str(uuid.uuid4())

        # submit a first tracker poker task
        poker_task = karton.core.Task(
            headers={
                "type": "peekaboo-job",
                "state": "new",
            },
            payload={
                "peekaboo-job-id": str(peekaboo_job_id),
                "file-name": sample.name,
                "content-type": content_type,
                "content-disposition": content_disposition,
            })
        self.send_task(poker_task)

        self.log.info(
            "%s:%s: Submitted with file-name %s, content-type %s and "
            "content-dispostion %s (%s)", task.root_uid, peekaboo_job_id,
            sample.name, content_type, content_disposition, poker_task.uid)


if __name__ == "__main__":
    c = PeekabooSubmitter(config)
    c.loop()
