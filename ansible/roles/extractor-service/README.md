extractor-service
=================

Install an extractor service.

Requirements
------------

Installations of redis and minio as well as karton-system are required for the
service to work.

Role Variables
--------------

* extractor\_service: extractor-deduper

* extractor\_user: "{{ extractor\_service }}"
* extractor\_group: "{{ extractor\_service }}"
* extractor\_user\_home: "/var/lib/{{ extractor\_user }}"

* extractor\_venv: "/opt/{{ extractor\_service }}"
* extractor\_config\_dir: "{{ extractor\_venv }}/etc"
* extractor\_config\_file: "{{ extractor\_config\_dir }}/extractor.ini"
* extractor\_service\_command: "{{ extractor\_venv }}/bin/{{ extractor\_service
    }} --config-file {{ extractor\_config\_file }}"

* extractor\_instances: 3

* karton\_log\_level: INFO

* minio\_access\_key: karton
* minio\_secret\_key: secret
* minio\_address: localhost:9000
* minio\_bucket: karton
* minio\_secure: 0

* redis\_host: localhost
* redis\_port: 6379

* extractor\_use\_deduper: True
* extractor\_use\_cache: True
* extractor\_reports\_key: extractor.reports
* extractor\_job\_cache\_key: "extractor.cache:"
* extractor\_correlator\_reports\_identity: extractor.correlator-for-job-

* extractor\_peekaboo\_url: "http://127.0.0.1:8100"
* extractor\_peekaboo\_backoff: 0.5
* extractor\_peekaboo\_retries: 5

* extractor\_peekaboo\_tracker\_job\_age\_cutoff: 600

* extractor\_cache\_responder\_age\_out\_interval: 60
* extractor\_cache\_responder\_max\_age: 240

* extractor\_deduper\_running\_key: extractor.running
* extractor\_deduper\_recheck\_interval: 2
* extractor\_deduper\_cutoff: 60
* extractor\_deduper\_gc\_interval: "{{ extractor\_deduper\_cutoff * 2 }}"

* extractor\_poker\_jobs\_key: extractor.jobs
* extractor\_poker\_recheck\_cutoff: 60
* extractor\_poker\_poking\_delay: 3
* extractor\_poker\_timeout: 1

* extractor\_api\_host: "127.0.0.1"
* extractor\_api\_port: 8200

Dependencies
------------

None.

Example Playbook
----------------


```
    - hosts: servers
      roles:
         - role: extractor-service
```

License
-------

GPL-3.0-only

Author Information
------------------

https://github.com/michaelweiser/extractor
