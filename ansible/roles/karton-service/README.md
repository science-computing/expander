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
* s3\_access\_key: karton
* s3\_secret\_key: secret
* s3\_address: http://localhost:9000
* s3\_bucket: karton
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
