#!/bin/bash

keyword=$1
page=$2

curl -X GET http://localhost:5000/kafka/crawl_rargb?keyword=$keyword\&page=$page
