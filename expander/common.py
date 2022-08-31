""" Common functionality used by multiple Kartons """

import datetime
import uuid

import karton.core
import karton.core.backend
import karton.core.task


class DelayedTask(karton.core.Task):
    def __init__(self, headers, payload=None, payload_persistent=None,
                 priority=None, parent_uid=None, root_uid=None,
                 orig_uid=None, uid=None, last_update=None, status=None,
                 error=None, delay_filters=None):
        """ Allow transfer of last_update and addition of additional filters to
        match. """
        super().__init__(
            headers, payload=payload, payload_persistent=payload_persistent,
            priority=priority, parent_uid=parent_uid, root_uid=root_uid,
            orig_uid=orig_uid, uid=uid, error=error)

        if last_update is not None:
            self.last_update = last_update
        if status is not None:
            self.status = status

        self.delay_filters = delay_filters

    def matches_filters(self, filters) -> bool:
        """ modified to include a separate set of delay filters provided by
        backend """
        if self.delay_filters is not None:
            filters = filters.copy()
            filters.extend(self.delay_filters)

        return super().matches_filters(filters)


class DelayingKartonBackend(karton.core.backend.KartonBackend):
    def __init__(self, config, identity=None, sync_identity=None):
        super().__init__(config, identity=identity)
        self.delay_filters = None
        self.delay_identity = None
        self.sync_identity = sync_identity

    def set_delay_identity(self, delay_filters, delay_identity):
        self.delay_filters = delay_filters
        self.delay_identity = delay_identity

    def get_queue_names(self, identity):
        queues = []

        # do not let delay queue switch syncs starve due to very long-running
        # normal tasks and incidentally avoid switching races between instances
        if self.sync_identity is not None:
            queues.extend(super().get_queue_names(self.sync_identity))

        queues.extend(super().get_queue_names(identity))

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
            status=task.status,
            delay_filters=self.delay_filters
        )


class DelayingKarton(karton.core.Karton):
    def __init__(self, config=None, identity=None, backend=None, timeout=1):
        self.timeout = timeout

        self.sync_identity = self.identity + ".delay-sync-%s" % uuid.uuid4()
        self.sync_task_type = self.identity + "-delay-sync"
        self.sync_headers = {"type": self.sync_task_type}
        self.sync_filters = [self.sync_headers]

        # inject our delaying backend by default
        if backend is None:
            # if identity is not passed via constructor get it from class
            if identity is None:
                identity = self.identity

            backend = DelayingKartonBackend(
                config, identity=identity, sync_identity=self.sync_identity)

        super().__init__(config=config, identity=identity, backend=backend)

        # add alternating delay queues later on
        self.current_delay_queue = 0
        self.next_delay_queue = 1
        self.delay_queues = ["one", "two"]
        self.delay_identity_template = self.identity + ".delay-%s"

        self.delay_filters = {}
        for delay_queue in self.delay_queues:
            self.delay_filters[delay_queue] = []
            for dfilt in self.delay_filters_template:
                delay_filter = dfilt.copy()
                delay_filter["delay-queue"] = delay_queue
                self.delay_filters[delay_queue].append(delay_filter)

        self.set_backend_delay_identity()

    def set_backend_delay_identity(self):
        delay_queue = self.delay_queues[self.current_delay_queue]
        delay_filters = self.delay_filters[delay_queue]

        # <identity>.delay-[one,two]
        delay_identity = self.delay_identity_template % delay_queue
        self.backend.set_delay_identity(delay_filters, delay_identity)

    def update_delay_queue(self, delay_queue=None):
        """ Switch to the next or an explicitly specified delay queue. """
        if delay_queue is None:
            delay_queue = self.current_delay_queue + 1

        self.current_delay_queue = delay_queue % len(self.delay_queues)
        self.next_delay_queue = (
            self.current_delay_queue + 1) % len(self.delay_queues)
        self.set_backend_delay_identity()

    def loop(self) -> None:
        self.log.info("Service %s started", self.identity)

        # Get the old binds and set the new ones atomically
        old_bind = self.backend.register_bind(self._bind)

        if not old_bind:
            self.log.info("Service binds created.")
        elif old_bind != self._bind:
            self.log.info(
                "Binds changed, old service instances should exit soon.")

        for task_filter in self.filters:
            self.log.info("Binding on: %s", task_filter)

        # MOD1: add additional identities for delaying tasks
        bind_redises = []
        for delay_queue in self.delay_queues:
            identity = self.delay_identity_template % delay_queue
            filters = self.delay_filters[delay_queue]
            self.log.info("Binding on delay queue: %s", filters)

            bind = karton.core.backend.KartonBind(
                identity=identity,
                info=f"Bind for delayed tasks",
                version=karton.core.__version__.__version__,
                filters=filters,
                persistent=True,
                service_version=self.__class__.version)

            # make these jobs be queued and routed to us as well
            self.backend.register_bind(bind)

            # create and keep around another redis connection to get us listed
            # as online consumer
            bind_redis = self.backend.make_redis(self.config, identity=identity)
            bind_redises.append(bind_redis)

        # MOD4: add additional identity for syncing delayers
        self.log.info("Binding on sync filters: %s", self.sync_filters)
        bind = karton.core.backend.KartonBind(
            identity=self.sync_identity,
            info="Bind for delayer sync",
            version=karton.core.__version__.__version__,
            filters=self.sync_filters,
            persistent=True,
            service_version=self.__class__.version)
        self.backend.register_bind(bind)
        bind_redis = self.backend.make_redis(
            self.config, identity=self.sync_identity)
        bind_redises.append(bind_redis)

        with self.graceful_killer():
            last_switch = datetime.datetime.now(datetime.timezone.utc)
            timeoutdelta = datetime.timedelta(seconds=self.timeout)
            while not self.shutdown:
                if self.backend.get_bind(self.identity) != self._bind:
                    self.log.info("Binds changed, shutting down.")
                    break

                # keep our secondary bind connections online
                for bind_redis in bind_redises:
                    bind_redis.ping()

                # MOD2: provide shorter timeout to task dequeue
                task = self.backend.consume_routed_task(
                    self.identity, self.timeout)

                now = datetime.datetime.now(datetime.timezone.utc)
                if not task or last_switch + timeoutdelta < now:
                    # MOD3: switch to next delay queue if blocking dequeue ran
                    # into timeout. This would leave delayed tasks starving if
                    # new tasks arrived continuously before the timeout
                    # expired.  Therefore we independently track the timeout
                    # between switches.
                    self.update_delay_queue()
                    last_switch = now

                    # tell our colleagues that we've just switched delay
                    # queues. This is somewhat inefficient as it (worst case)
                    # creates n^2 tasks (with n the number of delaying karton
                    # instances) for every delay queue switch. But it is
                    # required to keep everyone on the same delay queue because
                    # otherwise it can happen that two instances listening to
                    # different delay queues flood each other with delayed
                    # tasks as fast as they can, undermining the whole aspect
                    # of delaying them. For now we don't try to be smart about
                    # this and KISS until it turns out to be a problem.
                    delay_queue = self.delay_queues[self.current_delay_queue]
                    self.current_task = None
                    self.send_task(karton.core.Task(
                        headers=self.sync_headers,
                        payload={"delay-queue": delay_queue}))

                    if not task:
                        continue

                    # fall on through

                if task:
                    task_type = task.headers.get("type")
                    if task_type == self.sync_task_type:
                        self.backend.set_task_status(
                            task, karton.core.task.TaskState.FINISHED)

                        advertised_delay_queue = task.get_payload("delay-queue")
                        if advertised_delay_queue not in self.delay_queues:
                            self.log.warning(
                                "Unknown delay queue %s",
                                advertised_delay_queue)
                            continue

                        # try to sync up switch times (modulo travel delay of
                        # the task)
                        last_switch = now

                        new_queue = self.delay_queues.index(
                            advertised_delay_queue)
                        if new_queue == self.current_delay_queue:
                            continue

                        self.update_delay_queue(new_queue)
                        continue

                    self.internal_process(task)

        self.backend.unregister_bind(self.sync_identity)
