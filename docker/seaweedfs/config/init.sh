#!/bin/sh
sleep 20
echo 'starting config'
echo 's3.bucket.create --name soliplex-input' |weed shell -master=seaweedfs:9333 
echo 's3.bucket.create --name soliplex-storage-documents' |weed shell -master=seaweedfs:9333  
echo 's3.bucket.create --name soliplex-storage-markdown' |weed shell -master=seaweedfs:9333  
echo 's3.bucket.create --name soliplex-storage-json' |weed shell -master=seaweedfs:9333  
echo 's3.bucket.list ' |weed shell -master=seaweedfs:9333  

echo 's3.configure -access_key=soliplex_input_key -secret_key=soliplex_input_secret -user=soliplex_input -buckets=soliplex-input --actions Read,Write,List,Tagging --apply true' |weed shell -master=seaweedfs:9333   
echo 's3.configure -access_key=soliplex_storage_key -secret_key=soliplex_storage_secret -user=soliplex_storage -buckets=soliplex-storage-documents,soliplex-storage-markdown,soliplex-storage-json --actions Read,Write,List,Tagging --apply true' |weed shell -master=seaweedfs:9333   