karton-dashboard
================

Install the Karton dashboard.

Requirements
------------

Installations of redis and minio are required for the service to work.

Role Variables
--------------

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
         - role: karton-dashboard
```

License
-------

GPL-3.0-only

Author Information
------------------

https://github.com/michaelweiser/extractor
