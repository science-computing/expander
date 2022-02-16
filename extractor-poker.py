#!/home/michael/karton-venv/bin/python3

import datetime
import logging
import sys
import time
import uuid

import karton.core

EXTRACTOR_PEEKABOO_JOBS = "extractor.peekaboo.jobs"
EXTRACTOR_PEEKABOO_PER_JOB = "extractor.peekaboo.job:"

config = karton.core.Config(sys.argv[1])


class DelayedTask(karton.core.Task):
    def __init__(self, headers, payload=None, payload_persistent=None,
                 priority=None, parent_uid=None, root_uid=None,
                 orig_uid=None, uid=None, last_update=None, error=None,
                 delay_filters=None):
        """ Allow transfer of last_update and addition of additional filters to
        match. """
        super().__init__(
            headers, payload=payload, payload_persistent=payload_persistent,
            priority=priority, parent_uid=parent_uid, root_uid=root_uid,
            orig_uid=orig_uid, uid=uid, error=error)

        if last_update is not None:
            self.last_update = last_update

        self.delay_filters = delay_filters

    def matches_filters(self, filters) -> bool:
        """ modified to include a separate set of delay filters provided by
        backend """
        if self.delay_filters is not None:
            filters = filters.copy()
            filters.extend(self.delay_filters)
        
        return super().matches_filters(filters)


class NonBlockingKartonBackend(karton.core.backend.KartonBackend):
    def __init__(self, config):
        super().__init__(config)
        self.delay_filters = None
        self.delay_identity = None

    def set_delay_identity(self, delay_filters, delay_identity):
        self.delay_filters = delay_filters
        self.delay_identity = delay_identity

    def get_queue_names(self, identity):
        queues = super().get_queue_names(identity)

        if self.delay_identity is not None:
            queues.extend(super().get_queue_names(self.delay_identity))

        return queues

    def get_task(self, task_uid):
        """ modified to patch task so it matches main filters and is not
        rejected """
        task = super().get_task(task_uid)
        if task is None:
            return None

        # see Task.fork_task()
        return DelayedTask(
            headers=task.headers,
            payload=task.payload,
            payload_persistent=task.payload_persistent,
            priority=task.priority,
            parent_uid=task.parent_uid,
            root_uid=task.root_uid,
            uid=task.uid,
            last_update=task.last_update,
            delay_filters=self.delay_filters
        )

    def consume_queues(self, queues, timeout = 0):
        """ modified to be non-blocking """
        for queue in queues:
            item = self.redis.lpop(queue)
            if item:
                return queue, item

        return None


class ExtractorPoker(karton.core.Karton):
    identity = "extractor.poker"
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

    def __init__(self, config=None, identity=None, backend=None,
                 timeout=1, poking_delay=3):
        self.timeout = timeout
        self.poking_delay = poking_delay

        super().__init__(config=config, identity=identity, backend=backend)

        # add alternating delay queues later on
        self.current_delay_queue = 0
        self.next_delay_queue = 1
        self.delay_queues = ["one", "two"]
        self.delay_identity_template = "extractor.poker.delay-%s"

        self.delay_filters = dict()
        for delay_queue in self.delay_queues:
            self.delay_filters[delay_queue] = [{
                "type": "peekaboo-job",
                "state": "delayed",
                "delay-queue": delay_queue,
            }]

        self.set_backend_delay_identity()

    def set_backend_delay_identity(self):
        delay_queue  = self.delay_queues[self.current_delay_queue]
        delay_filters =  self.delay_filters[delay_queue]

        # extractor.poker.delay-[one,two]
        delay_identity = self.delay_identity_template % delay_queue
        self.backend.set_delay_identity(delay_filters, delay_identity)

    def update_delay_queue(self):
        self.current_delay_queue = (self.current_delay_queue + 1
            ) % len(self.delay_queues)
        self.next_delay_queue = (self.current_delay_queue + 1
            ) % len(self.delay_queues)
        self.set_backend_delay_identity()

    def process(self, task: karton.core.Task) -> None:
        state = task.headers.get("state")
        if state is None:
            self.log.warning(
                "Ignoring task %s without state: %s", task.uid, task.headers)
            return

        peekaboo_job_id = task.payload.get("peekaboo-job-id")
        if peekaboo_job_id is None:
            self.log.warning(
                "Ignoring task %s without Peekaboo job ID: %s",
                task.uid, task.headers)
            return

        peekaboo_job_hash = EXTRACTOR_PEEKABOO_PER_JOB + str(task.root_uid)

        headers = {
            "type": "peekaboo-job",
            "state": "to-track",
            # either telling the tracker where to submit bounce-back jobs or
            # directly submitting there for ourselves to look at again next
            # iteration
            "delay-queue": self.delay_queues[self.next_delay_queue],
        }

        if state == "new":
            # correlate this Peekaboo job with its extractor job for deciding
            # when all spawned jobs are finished
            self.backend.redis.hincrby(
                EXTRACTOR_PEEKABOO_JOBS, task.root_uid, 1)

            # also remember when we saw it first
            now = datetime.datetime.now(datetime.timezone.utc)
            first = now.isoformat()
            self.backend.redis.hset(peekaboo_job_hash, peekaboo_job_id, first)

            # pass on to tracker, leave headers above as-is
            task = task.derive_task(headers)
            self.send_task(task)
            self.log.info(
                    "%s:%s: Initially poked Peekaboo job tracker (%s)",
                task.root_uid, peekaboo_job_id, task.uid)
            return

        if state == "delayed":
            last_string = self.backend.redis.hget(
                peekaboo_job_hash, peekaboo_job_id)
            if not last_string:
                self.log.warning(
                    "%s:%s: Ignoring delayed Peekaboo job we've apparently "
                    "never seen before", task.root_uid, peekaboo_job_id)
                return

            last = datetime.datetime.fromisoformat(last_string)
            now = datetime.datetime.now(datetime.timezone.utc)
            poke_time = last + datetime.timedelta(seconds=self.poking_delay)

            if poke_time > now:
                # hand back to ourselves via the next delay queue for looking
                # at again
                headers["state"] = "delayed"
                self.log.info(
                    "%s:%s: Poking delay not yet expired, %s to go",
                    task.root_uid, peekaboo_job_id, poke_time - now)
            else:
                # send to tracker for tracking and remember when we did so for
                # potential next delay
                updated_last = now.isoformat()
                self.backend.redis.hset(
                    peekaboo_job_hash, peekaboo_job_id, updated_last)
                self.log.info(
                    "%s:%s: Poking Peekaboo job tracker",
                    task.root_uid, peekaboo_job_id)

            task = task.derive_task(headers)
            self.send_task(task)
            return

        if state == "done":
            # clean up
            self.backend.redis.hdel(peekaboo_job_hash, peekaboo_job_id)
            self.log.info(
                "%s:%s: Removed Peekaboo job from tracking",
                task.root_uid, peekaboo_job_id)

            jobs_left = self.backend.redis.hincrby(
                EXTRACTOR_PEEKABOO_JOBS, task.root_uid, -1)

            # all jobs tracked and reported - poke the correlator
            if jobs_left <= 0:
                self.backend.redis.hdel(EXTRACTOR_PEEKABOO_JOBS, task.root_uid)
                task = karton.core.Task(
                    headers={"type": "extractor-correlator-poke"},
                    payload={})
                self.send_task(task)

                self.log.info(
                    "%s: Poked correlator (%s)",
                    task.root_uid, task.uid)

            return

        self.log.warning(
            "Ignoring task %s with unknown state %s", task.uid, state)

    def poke(self) -> None:
        self.log.info("Service %s started", self.identity)

        # Get the old binds and set the new ones atomically
        old_bind = self.backend.register_bind(self._bind)

        if not old_bind:
            self.log.info("Service binds created.")
        elif old_bind != self._bind:
            self.log.info("Binds changed, old service instances should exit soon.")

        for task_filter in self.filters:
            self.log.info("Binding on: %s", task_filter)

        self.backend.set_consumer_identity(self.identity)

        # MOD1: add additional identities for delaying tasks
        for delay_queue in self.delay_queues:
            filters = self.delay_filters[delay_queue]
            self.log.info("Binding on delay queue: %s", filters)

            bind = karton.core.backend.KartonBind(
                identity=self.delay_identity_template % delay_queue,
                info=f"Poker bind for delayed tasks",
                version=karton.core.__version__.__version__,
                filters=filters,
                persistent=True,
                service_version=self.__class__.version)

            # make these jobs be queued and routed to us as well
            self.backend.register_bind(bind)

        try:
            while not self.shutdown:
                if self.backend.get_bind(self.identity) != self._bind:
                    self.log.info("Binds changed, shutting down.")
                    break

                task = self.backend.consume_routed_task(self.identity)
                if not task:
                    # MOD2: do one pass of all available tasks and then update
                    # the filters and sleep for the polling interval
                    time.sleep(self.timeout)

                    # switch to next delay queue
                    self.update_delay_queue()
                    continue

                self.internal_process(task)
        except KeyboardInterrupt as e:
            self.log.info("Hard shutting down!")
            raise e


if __name__ == "__main__":
    non_blocking_backend = NonBlockingKartonBackend(config)
    c = ExtractorPoker(config, backend=non_blocking_backend)
    c.poke()
