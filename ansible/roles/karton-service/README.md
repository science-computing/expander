karton-service
==============

Install a standard Karton service.

Requirements
------------

Installations of redis and minio are required for the service to work.

Role Variables
--------------

* karton\_service: karton-classifier
* karton\_package: "{{ karton\_service }}"
* karton\_os\_dependencies: []
* karton\_user: "{{ karton\_service }}"
* karton\_group: "{{ karton\_service }}"
* karton\_user\_home: "/var/lib/{{ karton\_user }}"
* karton\_venv: "/opt/{{ karton\_service }}"
* karton\_config\_dir: "{{ karton\_venv }}/etc"
* karton\_config\_file: "{{ karton\_config\_dir }}/karton.ini"
* karton\_bindir: "{{ karton\_venv }}/bin"
* karton\_extra\_args: ""
* karton\_command: "{{ karton\_service }}"
* karton\_service\_command: "{{ karton\_bindir }}/{{ karton\_command }} --config-file {{ karton\_config\_file }} {{ karton\_extra\_args }}"
* karton\_instances: 3
* karton\_log\_level: INFO
* minio\_access\_key: karton
* minio\_secret\_key: secret
* minio\_address: localhost:9000
* minio\_bucket: karton
* minio\_secure: 0
* redis\_host: localhost
* redis\_port: 6379

Dependencies
------------

None.

Example Playbook
----------------


```
    - hosts: servers
      roles:
         - role: karton-service
```

License
-------

GPL-3.0-only

Author Information
------------------

https://github.com/science-computing/expander
