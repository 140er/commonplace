### The release-gate test instance cleans up its workers

- The throwaway gateway used by the release-gate tests now closes its worker supervisor and handoff service when it exits. Before, it only stopped the workers, so a background handoff job could start them again and five real memory worker processes plus their threads were left running after the test file finished, adding load to parallel full runs.
