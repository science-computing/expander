#!/bin/bash

apt-get update
apt-get install redis virtualenv p7zip-full rar unace-nonfree cabextract lzip

virtualenv /opt/expander
/opt/expander/bin/pip install ..
/opt/expander/bin/pip install karton-dashboard karton-classifier karton-archive-extractor

mkdir /opt/expander/etc
cp ../expander.ini /opt/expander/etc/
useradd -m expander

mkdir -p /var/lib/minio/data
useradd -m minio-user
chown -R minio-user: /var/lib/minio/data

wget https://dl.min.io/server/minio/release/linux-amd64/minio_20220314182524.0.0_amd64.deb -O minio.deb
dpkg -i minio.deb
rm minio.deb

cp default/minio /etc/default

systemctl restart minio

cp -r *.service* /etc/systemd/system

# brace expansion is a bashism
for i in karton-{system,logger,classifier,archive-extractor} ; do
	echo -e "[Install]\nWantedBy=multi-user.target" > /etc/systemd/system/$i@.service
	systemctl enable $i@{1,2,3}
	systemctl start $i@{1,2,3}
done

cp karton-dashboard.service /etc/systemd/system/karton-dashboard.service
systemctl enable karton-dashboard
systemctl start karton-dashboard

for i in expander-{deduper,cache-responder,peekaboo-submitter,poker,peekaboo-tracker,correlator} ; do
	echo -e "[Install]\nWantedBy=multi-user.target" > /etc/systemd/system/$i@.service

	systemctl enable $i@{1,2,3}
	systemctl start $i@{1,2,3}
done

cp expander-api.service /etc/systemd/system/expander-api.service
systemctl enable expander-api
systemctl start expander-api
