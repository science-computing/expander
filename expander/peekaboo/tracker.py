""" A Karton that tracks jobs in Peekaboo and retrieves reports. """

import datetime
import sys
import urllib.parse

import karton.core
import karton.core.backend
import karton.core.__version__
import requests
import schema
import urllib3.util

from .. import __version__


class ExpanderPeekabooTracker(karton.core.Karton):
    """ expander peekaboo tracker karton """
    identity = "expander.peekaboo-tracker"
    version = __version__
    filters = [
        {
            "type": "peekaboo-job",
            "state": "to-track",
        }
    ]

    def __init__(self, config=None, identity=None, backend=None,
                 url="http://127.0.0.1:8100", retries=5, backoff=0.5,
                 job_age_cutoff=600,
                 reports_identity="expander.correlator-for-job-"):
        super().__init__(config=config, identity=identity, backend=backend)

        self.reports_identity = config.get(
            "expander", "correlator_reports_identity",
            fallback=reports_identity)

        self.job_age_cutoff = datetime.timedelta(seconds=config.getint(
            "expanderpeekabootracker", "job_age_cutoff",
            fallback=job_age_cutoff))

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

    def track(self, job_id):
        try:
            response = self.session.get(urllib.parse.urljoin(
                self.url, f"/v1/report/{job_id}"))
        except requests.exceptions.RequestException as error:
            return None, str(error)

        # no report yet
        if response.status_code == 404:
            return None, None

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
                "result": str,
                "reason": str,
                schema.Optional("report"): list,
            }).validate(response.json())
        except (ValueError, schema.SchemaError) as error:
            return None, str(error)

        return json, None

    def process(self, task: karton.core.Task) -> None:
        peekaboo_job_id = None
        report_payload = None
        job_state = task.get_payload("job-state")
        if job_state == "submit-failed":
            # submit to peekaboo had failed :(
            report_payload = {"result": "failed"}
            reason = task.get_payload("failure-reason")
            if reason is not None:
                report_payload[
                    "reason"] = f"submit to peekaboo failed: {reason}"
        elif job_state == "archive-ignored":
            # no job was submitted because the sample was ignored
            report_payload = {
                "result": "ignored",
                "reason": "archive was ignored",
            }
        else:
            peekaboo_job_id = task.get_payload("peekaboo-job-id")
            if not isinstance(peekaboo_job_id, int):
                self.log.warning(
                    "%s:%s: Dropping job with missing or invalid Peekaboo "
                    "job ID", task.root_uid, peekaboo_job_id)
                return

            report_payload, error = self.track(peekaboo_job_id)
            if report_payload is None:
                if error is None:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    since_payload = task.get_payload("tracking-since")
                    if since_payload is None:
                        task.add_payload("tracking-since", now.isoformat())
                    else:
                        since = datetime.datetime.fromisoformat(since_payload)
                        if since + self.job_age_cutoff < now:
                            error = "job got too old: %s" % (now - since)

                if error is None:
                    # not done yet - bounce back to the poker
                    delay_task = task.derive_task({
                        "type": "peekaboo-job",
                        "state": "delayed",
                        "delay-queue": task.headers.get("delay-queue"),
                    })
                    self.send_task(delay_task)
                    self.log.info(
                        "%s:%s: Told poker that it needs more tracking (%s)",
                        task.root_uid, peekaboo_job_id, delay_task.uid)
                    return

                reason = f"Tracking Peekaboo job failed: {error}"
                self.log.warning(
                    "%s:%s: %s", task.root_uid, peekaboo_job_id, reason)
                report_payload = {
                    "result": "failed",
                    "reason": reason,
                }

        # notify poker that this job is done and needs no more poking
        done_task = karton.core.Task(
            headers={
                "type": "peekaboo-job",
                "state": "done",
            })

        # metadata from persistent payload (job id, file-name) is added
        # automatically here
        self.send_task(done_task)
        self.log.info(
            "%s:%s: Told poker that it's done (%s)",
            task.root_uid, peekaboo_job_id, done_task.uid)

        # register a persistent queue for this expander job (if there isn't one
        # already) where to park the job reports until all jobs are done
        bind = None
        report_identity = self.reports_identity + str(task.root_uid)
        try:
            bind = self.backend.get_bind(report_identity)
        except TypeError:
            pass

        # have to match the correlator per-job bind filters or we'll get "Task
        # rejected because binds are no longer valid." there
        headers = {
            "type": "peekaboo-report",
            # only for routing to the job-specific persistent correlator parking queue
            "expander-job-id": task.root_uid,
        }

        if bind is None:
            bind = karton.core.backend.KartonBind(
                identity=report_identity,
                info=("Correlator queue persistence bind for job "
                      f"{task.root_uid} created by tracker"),
                version=karton.core.__version__.__version__,
                # filters are headers of our tasks, obvs.
                filters=[headers],
                persistent=True,
                service_version=self.__class__.version)
            self.backend.register_bind(bind)

        report_task = karton.core.Task(
            headers=headers,
            payload=report_payload)

        # metadata from persistent payload is added automatically here
        self.send_task(report_task)
        self.log.info(
            "%s:%s: Submitted report (%s)",
            task.root_uid, peekaboo_job_id, report_task.uid)

        # do not do lengthy processing down here because it aggravates a race
        # condition with the all-jobs-finished check in the poker


if __name__ == "__main__":
    ExpanderPeekabooTracker.main()
