""" A Karton that pokes job trackers and correlators based on conditions. """

import datetime
import sys

import karton
import karton.core
import karton.core.inspect
import karton.core.task

from . import __version__
from .common import DelayingKarton


class ExpanderPoker(DelayingKarton):
    """ expander poker karton """
    identity = "expander.poker"
    version = __version__
    filters = [
        {
            "type": "peekaboo-job",
            "state": "new",
        }, 
        {
            "type": "peekaboo-job",
            "state": "done",
        }, 
    ]

    delay_filters_template = [
        {
            "type": "peekaboo-job",
            "state": "delayed",
        }, {
            "type": "peekaboo-job",
            "state": "done-recheck",
        }
    ]

    def __init__(self, config=None, identity=None, backend=None,
                 timeout=1, poking_delay=3):
        self.poking_delay = datetime.timedelta(seconds=config.getint(
            "expanderpoker", "poking_delay", fallback=poking_delay))
        timeout = config.getint(
            "expanderpoker", "timeout", fallback=timeout)

        self.jobs_key = config.get(
            "expander", "jobs_key", fallback="expander.jobs")

        # 60 seconds should be way enough for all race-conditions to resolve
        # themselves
        self.recheck_cutoff = datetime.timedelta(seconds=config.getint(
            "expanderpoker", "cutoff", fallback=60))

        super().__init__(
            config=config, identity=identity, backend=backend, timeout=timeout)

    def process(self, task: karton.core.Task) -> None:
        state = task.headers.get("state")
        if state is None:
            self.log.warning(
                "Ignoring task %s without state: %s", task.task_uid, task.headers)
            return

        headers = {
            "type": "peekaboo-job",
            "state": "to-track",
            # either telling the tracker where to submit bounce-back jobs or
            # directly submitting there for ourselves to look at again next
            # iteration
            "delay-queue": self.delay_queues[self.next_delay_queue],
        }

        if state == "new":
            # correlate this job with its expander job for deciding
            # when all spawned jobs are finished
            self.backend.redis.hincrby(self.jobs_key, task.root_uid, 1)

            # pass on to tracker, leave headers above as-is
            tracker_task = task.derive_task(headers)

            # remember in the payload when we saw it first
            tracker_task.add_payload("last-poke", datetime.datetime.now(
                datetime.timezone.utc).isoformat())

            self.send_task(tracker_task)
            self.log.info(
                "%s:%s: Initially poked job tracker (%s)",
                task.root_uid, task.task_uid, tracker_task.task_uid)
            return

        if state == "delayed":
            last_string = task.get_payload("last-poke")
            if last_string is None:
                self.log.warning(
                    "%s:%s: Ignoring delayed task we've apparently "
                    "never seen before", task.root_uid, task.task_uid)
                return

            last = datetime.datetime.fromisoformat(last_string)
            now = datetime.datetime.now(datetime.timezone.utc)
            poke_time = last + self.poking_delay

            if poke_time > now:
                # hand back to ourselves via the next delay queue for looking
                # at again
                headers["state"] = "delayed"
                self.log.info(
                    "%s:%s: Poking delay not yet expired, %s to go",
                    task.root_uid, task.task_uid, poke_time - now)

                # do not update last-poke payload, so it expires
            else:
                # send to tracker for tracking and remember when we did so for
                # potential next delay
                task.remove_payload("last-poke")
                task.add_payload("last-poke", now.isoformat())

                self.log.info(
                    "%s:%s: Poking job tracker",
                    task.root_uid, task.task_uid)

            task = task.derive_task(headers)
            self.send_task(task)
            return

        if state in ["done", "done-recheck"]:
            # clean up
            if state != "done-recheck":
                # do not spam the log on rechecks
                self.log.info(
                    "%s:%s: Removed task from tracking",
                    task.root_uid, task.task_uid)

            # NOTE: we consider this an atomic operation which is preventing us
            # from producing multiple correlator pokes
            jobs_left = self.backend.redis.hincrby(
                self.jobs_key, task.root_uid, -1)
            if jobs_left < 0:
                # NOTE: The count will become negative legitimately if two
                # pokers had been racing each other in concluding whether they
                # were handling the last task of this job and one of them poked
                # the correlator and subsequently deleted the jobs key. The
                # other would have reincremented it (which got lost by the
                # deletion) and generated the done-recheck which we're
                # processing now. Due to the nonexistance of the jobs key, the
                # decrement above produced a negative key value. We detect this
                # to prevent a duplicate correlator poke and delete the key
                # again for cleanup.
                self.log.debug(
                    "%s:%s: Job count went negative through race with "
                    "colleague (%d) - refraining from poking the correlator "
                    "again", task.root_uid, task.task_uid, jobs_left)
                self.backend.redis.hdel(self.jobs_key, task.root_uid)
                return

            if jobs_left != 0:
                return

            # make sure no more jobs are coming. get_analysis() of recent
            # Karton is much cheaper than get_all_tasks().
            state = karton.core.inspect.KartonState(self.backend)

            # pending_tasks already ignores crashed tasks
            # NOTE: We ignore crashed tasks because they may not
            # belong to our part of the pipeline and our getting to
            # this point makes it extremely unlikely that it does.
            for other_task in state.get_analysis(task.root_uid).pending_tasks:
                if other_task.uid == task.uid:
                    # ignore ourselves
                    continue

                if other_task.status == karton.core.task.TaskState.FINISHED:
                    # ignore finished tasks. Would be removed by karton-system
                    # eventually but ignoring them here speeds up the decision
                    # making.
                    continue

                if other_task.headers.get("type") in [
                        "peekaboo-report", "expander-correlator-poke",
                        "expander-dedupe-hold"]:
                    # ignore reports and correlator pokes as well as
                    # deduper holds
                    continue

                # NOTE: This check is vulnerable to dangling forks in the
                # pipeline, i.e. binds which are no longer serviced. Tasks
                # will be forked and routed to it and linger there in
                # Spawned state. The only way to detect that would be to
                # model the direct route of the pipeline up to the poker in
                # this check which defeats the purpose of having a flexible
                # pipeline. Therefore operators have to make sure to have
                # no dangling binds.
                now = datetime.datetime.now(datetime.timezone.utc)
                recheck_start = now
                recheck_start_payload = task.get_payload("recheck-start")
                if recheck_start_payload is not None:
                    recheck_start = datetime.datetime.fromisoformat(
                        recheck_start_payload)

                delay_and_recheck = True
                if recheck_start + self.recheck_cutoff < now:
                    self.log.warning(
                        "%s: Lingering task %s is blocking correlation.",
                        task.root_uid, other_task.task_uid)

                    if (other_task.status ==
                            karton.core.task.TaskState.SPAWNED):
                        self.log.info(
                            "%s: Blocker task is likely caused by "
                            "dangling bind. Ignoring and going ahead with "
                            "correlation after %s.",
                            task.root_uid, now - recheck_start)
                        delay_and_recheck = False
                else:
                    self.log.info(
                        "%s: More jobs appear to be coming: %s",
                        task.root_uid, other_task.task_uid)

                # there are race conditions between a submitter sending the
                # peekaboo-job task to us (and us having processed it) and
                # finishing its own task as well as a tracker sending off
                # the done task (and us having processed it) and finishing
                # its own task. This might leave the reports starving if we
                # just dropped this done task expecting another to be
                # produced by coming jobs. So we poke ourselves for the
                # next iteration.
                if delay_and_recheck:
                    # re-increment the pending jobs counter so upon re-check we
                    # can again atomically decide if the job count reached zero
                    # or not
                    self.backend.redis.hincrby(self.jobs_key, task.root_uid, 1)

                    headers["state"] = "done-recheck"
                    task = task.derive_task(headers)

                    # add the first time we started re-checks only once
                    if not task.has_payload("recheck-start"):
                        task.add_payload(
                            "recheck-start", recheck_start.isoformat())

                    self.send_task(task)
                    return

            # all jobs tracked and reported - poke the correlator
            self.backend.redis.hdel(self.jobs_key, task.root_uid)
            task = karton.core.Task(
                headers={"type": "expander-correlator-poke"})
            self.send_task(task)

            self.log.info(
                "%s: Poked correlator (%s)",
                task.root_uid, task.task_uid)
            return

        self.log.warning(
            "%s:%s: Ignoring task with unknown state %s",
            task.root_uid, task.task_uid, state)


if __name__ == "__main__":
    ExpanderPoker.main()
