expander-service
=================

Install an expander service.

Requirements
------------

Installations of redis and minio as well as karton-system are required for the
service to work.

Role Variables
--------------

* expander\_service: expander-deduper

* expander\_user: "{{ expander\_service }}"
* expander\_group: "{{ expander\_service }}"
* expander\_user\_home: "/var/lib/{{ expander\_user }}"

* expander\_venv: "/opt/{{ expander\_service }}"
* expander\_config\_dir: "{{ expander\_venv }}/etc"
* expander\_config\_file: "{{ expander\_config\_dir }}/expander.ini"
* expander\_service\_command: "{{ expander\_venv }}/bin/{{ expander\_service
    }} --config-file {{ expander\_config\_file }}"

* expander\_instances: 3

* karton\_log\_level: INFO

* s3\_access\_key: karton
* s3\_secret\_key: secret
* s3\_address: http://localhost:9000
* s3\_bucket: karton

* redis\_host: localhost
* redis\_port: 6379

* expander\_use\_deduper: True
* expander\_use\_cache: True
* expander\_reports\_key: expander.reports
* expander\_job\_cache\_key: "expander.cache:"
* expander\_correlator\_reports\_identity: expander.correlator-for-job-

* expander\_peekaboo\_url: "http://127.0.0.1:8100"
* expander\_peekaboo\_backoff: 0.5
* expander\_peekaboo\_retries: 5

* expander\_peekaboo\_tracker\_job\_age\_cutoff: 600

* expander\_cache\_responder\_age\_out\_interval: 60
* expander\_cache\_responder\_max\_age: 240

* expander\_deduper\_running\_key: expander.running
* expander\_deduper\_recheck\_interval: 2
* expander\_deduper\_cutoff: 60
* expander\_deduper\_gc\_interval: "{{ expander\_deduper\_cutoff * 2 }}"

* expander\_poker\_jobs\_key: expander.jobs
* expander\_poker\_recheck\_cutoff: 60
* expander\_poker\_poking\_delay: 3
* expander\_poker\_timeout: 1

* expander\_api\_host: "127.0.0.1"
* expander\_api\_port: 8200

Dependencies
------------

None.

Example Playbook
----------------


```
    - hosts: servers
      roles:
         - role: expander-service
```

License
-------

GPL-3.0-only

Author Information
------------------

https://github.com/science-computing/expander
