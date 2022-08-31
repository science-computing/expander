""" A Karton that submits samples to Peekaboo for analysis. """

import sys
import urllib.parse

import karton.core
import requests
import schema
import urllib3.util

from .. import __version__


class ExpanderPeekabooSubmitter(karton.core.Karton):
    """ expander peekaboo submitter karton """
    identity = "expander.peekaboo-submitter"
    version = __version__
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

    def __init__(self, config=None, identity=None, backend=None,
                 url="http://127.0.0.1:8100", retries=5, backoff=0.5):
        super().__init__(config=config, identity=identity, backend=backend)

        self.url = config.get("expanderpeekaboo", "url", fallback=url)

        retries = config.getint(
            "expanderpeekaboo", "retries", fallback=retries)
        # no getfloat in karton Config (yet)
        backoff = float(config.get(
            "expanderpeekaboo", "backoff", fallback=backoff))

        retry_config = urllib3.util.Retry(
            total=retries, backoff_factor=backoff,
            allowed_methods=frozenset({"POST"}),
            status_forcelist=frozenset({413, 429, 500, 503}),
            raise_on_status=False, raise_on_redirect=False)
        retry_adapter = requests.adapters.HTTPAdapter(max_retries=retry_config)
        self.session = requests.session()
        self.session.mount("http://", retry_adapter)
        self.session.mount("https://", retry_adapter)

    def submit(self, content, filename=None, content_type=None,
               content_disposition=None):
        files = {"file": (filename, content, content_type)}
        headers = {"x-content-disposition": content_disposition}

        try:
            # NOTE: As of this writing, requests via urllib3 1.26.x encodes
            # filenames according to a WHATWG HTML5 spec draft using
            # percent encoding and backslash escaping. Peekaboo via sanic
            # only supports part of the current WHATWG HTML5 spec (no
            # longer draft) by reverting double quote escaping (%22 -> ").
            # We cannot (without serious hacking) switch requests to use
            # RFC2231 encoding from here. Therefore filenames containing
            # special characters can not currently be correctly transferred
            # using this command. urllib3 has already been adjusted to
            # default to the current version of the HTML5 escaping scheme.
            # So sanic hopefully doing the same there's a chance this will
            # sort itself out by itself eventually.
            response = self.session.post(urllib.parse.urljoin(
                self.url, "/v1/scan"), files=files, headers=headers)
        except requests.exceptions.RequestException as error:
            return None, str(error)

        if response.status_code != 200:
            try:
                json_error = schema.Schema({
                    "message": str,
                }).validate(response.json())
                return None, json_error["message"]
            except (ValueError, schema.SchemaError):
                return None, str(error)

        try:
            json = schema.Schema({
                "job_id": int,
            }).validate(response.json())
        except (ValueError, schema.SchemaError) as error:
            return None, str(error)

        # job ID is opaque to us - no further checks
        return json["job_id"], None

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
            peekaboo_job_id, failure_reason = self.submit(
                sample.content, file_name, content_type,
                content_disposition)
            if peekaboo_job_id is not None:
                self.log.info(
                    "%s:%s: Submitted with file-name %s, content-type %s and "
                    "content-dispostion %s", task.root_uid, peekaboo_job_id,
                    file_name, content_type, content_disposition)

                poker_task.add_payload("job-state", "submitted")
                poker_task.add_payload(
                    "peekaboo-job-id", peekaboo_job_id, persistent=True)
            else:
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

        root_sample = task.get_payload("root-sample")
        if root_sample is not None:
            poker_task.add_payload(
                "root-sample", root_sample, persistent=True)

        poker_task.add_payload("sha256", sample.sha256, persistent=True)

        self.send_task(poker_task)

        # do not do lengthy processing down here because it aggravates a race
        # condition with the all-jobs-finished check in the poker


if __name__ == "__main__":
    ExpanderPeekabooSubmitter.main()
