import { join } from "node:path";
import { ActivationManager } from "./activation.ts";
import { AgentRuntimeManager } from "./agent-runtime.ts";
import { loadConfig } from "./config.ts";
import { Controller } from "./controller.ts";
import { GitHubPublisher } from "./github.ts";
import { GuestBroker } from "./guest-broker.ts";
import { SocketServer } from "./socket-server.ts";
import { Store } from "./store.ts";
import { privateDirectory, redactError } from "./util.ts";

const config = loadConfig();

// Gondolin ingress clients can finish rejecting asynchronously while trusted
// guest processes are replaced during startup recovery. Register the existing
// redacted safety handlers before any VM work begins so those expected
// connection teardowns cannot terminate the controller mid-recovery.
process.on("uncaughtException", (error) => {
  console.error(`uncaught controller error: ${redactError(error)}`);
});
process.on("unhandledRejection", (error) => {
  console.error(`unhandled controller rejection: ${redactError(error)}`);
});

if (!config.enabled) {
  console.log("ttd development agent is disabled");
  const keepAlive = setInterval(() => undefined, 3_600_000);
  const stop = () => {
    clearInterval(keepAlive);
    process.exit(0);
  };
  process.once("SIGINT", stop);
  process.once("SIGTERM", stop);
} else {
  await privateDirectory(config.stateRoot);
  const store = new Store(join(config.stateRoot, "controller.sqlite3"), config.maxSlots);
  const guest = new GuestBroker(config);
  await guest.start();
  const activation = new ActivationManager(store, guest);
  const publisher = new GitHubPublisher(config, store, guest);
  const runtime = new AgentRuntimeManager(store, config, guest, activation, publisher);
  const controller = new Controller(store, runtime, config);
  const socket = new SocketServer(config.socketPath, config.socketGid, controller, store);
  await socket.listen();
  console.log(`ttd development agent listening on ${config.socketPath}`);
  const prPoller = setInterval(() => {
    void runtime.reconcilePullRequests().catch(() => undefined);
  }, 60_000);
  let policyDegraded = false;
  const policyPoller = setInterval(() => {
    void runtime.syncPolicies().then(() => {
      if (policyDegraded) console.log("slot policy sync recovered");
      policyDegraded = false;
    }).catch((error: unknown) => {
      if (!policyDegraded) console.error(`slot policy sync degraded: ${redactError(error)}`);
      policyDegraded = true;
    });
  }, 5_000);
  const ingressDegradedSlots = new Set<number>();
  let ingressDegraded = false;
  const ingressPoller = setInterval(() => {
    void guest.ensureHealthy().then(() => {
      if (ingressDegraded) console.log("Gondolin ingress recovered");
      ingressDegraded = false;
      for (const id of ingressDegradedSlots) {
        const slot = store.slots().find((candidate) => candidate.id === id);
        if (slot?.owner) store.setSlotHealth(id, "healthy");
      }
      ingressDegradedSlots.clear();
    }).catch((error: unknown) => {
      if (!ingressDegraded) console.error(`Gondolin ingress degraded: ${redactError(error)}`);
      ingressDegraded = true;
      for (const slot of store.slots()) {
        if (!slot.owner) continue;
        if (slot.health !== "degraded") ingressDegradedSlots.add(slot.id);
        store.setSlotHealth(slot.id, "degraded");
      }
    });
  }, 5_000);
  let stopping = false;
  const stop = async () => {
    if (stopping) return;
    stopping = true;
    try {
      clearInterval(prPoller);
      clearInterval(policyPoller);
      clearInterval(ingressPoller);
      await socket.close();
      await controller.waitForIdle();
      await runtime.shutdown();
      store.close();
      process.exit(0);
    } catch (error) {
      console.error(`shutdown failed: ${redactError(error)}`);
      process.exit(1);
    }
  };
  process.once("SIGINT", () => void stop());
  process.once("SIGTERM", () => void stop());
}
