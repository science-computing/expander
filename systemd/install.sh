#!/bin/bash

apt-get update
apt-get install redis virtualenv p7zip-full rar unace-nonfree cabextract lzip

virtualenv /opt/extractor
/opt/extractor/bin/pip install ..
/opt/extractor/bin/pip install karton-dashboard karton-classifier karton-archive-extractor

mkdir /opt/extractor/etc
cp ../extractor.ini /opt/extractor/etc/
useradd -m extractor

wget https://dl.min.io/server/minio/release/linux-amd64/minio_20220314182524.0.0_amd64.deb -O minio.deb
dpkg -i minio.deb
rm minio.deb

cp default/minio /etc/default
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

for i in extractor-{deduper,cache-responder,peekaboo-submitter,poker,peekaboo-tracker,correlator} ; do
	echo -e "[Install]\nWantedBy=multi-user.target" > /etc/systemd/system/$i@.service

	systemctl enable $i@{1,2,3}
	systemctl start $i@{1,2,3}
done

cp extractor-api.service /etc/systemd/system/extractor-api.service
systemctl enable extractor-api
systemctl start extractor-api
