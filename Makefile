.PHONY: test up demo

test:
	python -m pytest -q

up:
	docker compose up --build

demo:
	set -a && . ./.env && set +a && \
	ALICE=$$(python -c 'from wso2 import user_uuid; print(user_uuid("alice"))') && \
	BOB=$$(python -c 'from wso2 import user_uuid; print(user_uuid("bob"))') && \
	curl -sf -X POST http://localhost:5000/add_spammer \
	  -H "Content-Type: application/json" -H "X-API-Key: $$API_KEY" \
	  -d "{\"uuid\":\"$$BOB\",\"username\":\"bob\"}" && \
	curl -sf -X POST http://localhost:5000/events \
	  -H "Content-Type: application/json" -H "X-User-Uuid: $$ALICE" -H "X-User-Name: alice" \
	  -d '{"description":"checkout-api outage, payments are down"}' && \
	curl -sf -X POST http://localhost:5000/events \
	  -H "Content-Type: application/json" -H "X-User-Uuid: $$ALICE" -H "X-User-Name: alice" \
	  -d '{"description":"edge-proxy latency is high and checkout is slow"}' && \
	curl -sf -X POST http://localhost:5000/events \
	  -H "Content-Type: application/json" -H "X-User-Uuid: $$ALICE" -H "X-User-Name: alice" \
	  -d '{"description":"nightly report finished, informational notice"}' && \
	curl -sf -X POST http://localhost:5000/events \
	  -H "Content-Type: application/json" -H "X-User-Uuid: $$BOB" -H "X-User-Name: bob" \
	  -d '{"description":"checkout-api outage, payments are down"}' && \
	printf '\nDesk http://localhost:5000\nMailpit http://localhost:8025\nGrafana http://localhost:3000\n'
