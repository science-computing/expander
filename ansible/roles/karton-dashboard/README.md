karton-dashboard
================

Install the Karton dashboard.

Requirements
------------

Installations of redis and minio are required for the service to work.

Role Variables
--------------

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
         - role: karton-dashboard
```

License
-------

GPL-3.0-only

Author Information
------------------

https://github.com/science-computing/expander
