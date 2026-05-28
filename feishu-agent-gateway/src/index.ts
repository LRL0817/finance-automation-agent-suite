import { CommandController } from "./commands/controller.js";
import { loadConfig } from "./config.js";
import { GatewayDatabase } from "./db/database.js";
import { FeishuEventGateway } from "./feishu/event-gateway.js";
import { FeishuMessageClient } from "./feishu/message-client.js";
import { logger } from "./logger.js";
import { AttachmentCleaner } from "./maintenance/attachment-cleaner.js";
import { ReportController } from "./reports/report-controller.js";
import { createRunners } from "./runners/index.js";
import { AccessControl } from "./security/access-control.js";
import { TaskManager } from "./tasks/task-manager.js";

async function main(): Promise<void> {
  const config = loadConfig();
  const db = new GatewayDatabase(config.sqlitePath);
  if (config.security.defaultProjectPath) {
    db.applyDefaultProjectPath(config.security.defaultProjectPath);
  }
  db.restoreMissingRunnerThreadsFromTasks();
  const messenger = new FeishuMessageClient(config);
  const runners = createRunners(config);
  const accessControl = new AccessControl(config);
  const taskManager = new TaskManager(config, db, runners, messenger);
  const controller = new CommandController(config, db, accessControl, taskManager, messenger);
  const reportController = new ReportController(config, db, accessControl, taskManager);
  const gateway = new FeishuEventGateway(config, controller, reportController);
  const attachmentCleaner = new AttachmentCleaner(config);

  process.on("SIGINT", () => {
    void shutdown("SIGINT", gateway, db, attachmentCleaner);
  });
  process.on("SIGTERM", () => {
    void shutdown("SIGTERM", gateway, db, attachmentCleaner);
  });

  attachmentCleaner.start();
  gateway.start();
  logger.info("Feishu agent gateway is ready");
}

async function shutdown(
  signal: string,
  gateway: FeishuEventGateway,
  db: GatewayDatabase,
  attachmentCleaner: AttachmentCleaner
): Promise<void> {
  logger.info(`Received ${signal}, shutting down`);
  attachmentCleaner.stop();
  await gateway.stop();
  db.close();
  process.exit(0);
}

main().catch((error) => {
  logger.error("Failed to start gateway", error);
  process.exit(1);
});
