# Optional developer shortcuts (Unix make). On Windows, use equivalent `py -m` commands.

.PHONY: local gateway api worker ar-install ar-build ar-dev

ENTITY ?= canary

local:
	py -m ngram.main run $(ENTITY)

gateway:
	py -m ngram.inference_gateway

api:
	py -m ngram.main api --host 0.0.0.0 --port 8080

worker:
	py -m ngram.main worker $(ENTITY)

ar-install:
	npm --prefix ngramAR install

ar-build:
	npm --prefix ngramAR run build

ar-dev:
	npm --prefix ngramAR run ngram-ar -- dev shells/$(ENTITY)
