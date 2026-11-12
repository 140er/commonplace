### Compute map in the Deck look

- In the Deck look the Compute screen is the only map: it draws the shared topology canvas from `GET /api/topology`, and `#/compute-map` resolves to it. When the topology is empty or unavailable it draws the hub and the enrolled runner machines instead, in the states Ready, Waiting for attestation and Off, with nothing about liveness, sites or routes.
