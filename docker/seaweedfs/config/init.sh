#!/bin/sh
sleep 20
echo 'starting config'
echo 's3.bucket.create --name soliplex-input' |weed shell -master=seaweedfs:9333
echo 's3.bucket.create --name soliplex-artifacts' |weed shell -master=seaweedfs:9333
echo 's3.bucket.create --name soliplex-lancedb' |weed shell -master=seaweedfs:9333
echo 's3.bucket.list ' |weed shell -master=seaweedfs:9333

echo 's3.configure -access_key=soliplex_input_key -secret_key=soliplex_input_secret -user=soliplex_input -buckets=soliplex-input --actions Read,Write,List,Tagging --apply true' |weed shell -master=seaweedfs:9333
echo 's3.configure -access_key=soliplex -secret_key=soliplex -user=soliplex -buckets=soliplex-artifacts,soliplex-lancedb --actions Read,Write,List,Tagging --apply true' |weed shell -master=seaweedfs:9333
