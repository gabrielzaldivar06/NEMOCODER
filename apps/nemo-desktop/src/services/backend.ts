/**
 * Backend service for Nemocode Desktop
 * 
 * This module provides typed wrappers around Tauri commands for managing
 * the Python backend lifecycle and settings persistence.
 */

import { invoke } from "@tauri-apps/api/tauri";

/**
 * Backend process information
 */
export interface BackendInfo {
  pid: number;
  port: number;
  status: "running" | "starting" | "already_running" | "error";
}

/**
 * Backend health status
 */
export interface HealthStatus {
  status: string;
  reachable: boolean;
  message: string;
}

export type SetupStatus = "ready" | "degraded" | "setup_required" | "fatal_error";

export interface SetupDiagnostics {
  lmStudioFound: boolean;
  nemoDbFound: boolean;
  diskSpaceOk: boolean;
  permissionsOk: boolean;
  ready: boolean;
  status: SetupStatus;
  issues: string[];
}

/**
 * Application settings
 */
export interface AppSettings {
  backend_port: number;
  lm_studio_url: string;
  nemo_database_path: string;
  auto_start_backend: boolean;
  theme: "light" | "dark";
}

/**
 * Backend service for managing the Python backend lifecycle
 */
export class BackendService {
  private static instance: BackendService;
  private retryCount = 0;
  private maxRetries = 3;

  private constructor() {}

  /**
   * Get singleton instance of BackendService
   */
  static getInstance(): BackendService {
    if (!BackendService.instance) {
      BackendService.instance = new BackendService();
    }
    return BackendService.instance;
  }

  /**
   * Start the Python backend process
   * Retries up to 3 times if initial startup fails
   */
  async startBackend(): Promise<BackendInfo> {
    try {
      this.retryCount = 0;
      return await this.invokeStartBackend();
    } catch (error) {
      console.error("Failed to start backend:", error);
      throw error;
    }
  }

  /**
   * Internal method with retry logic
   */
  private async invokeStartBackend(): Promise<BackendInfo> {
    try {
      const result = await invoke<BackendInfo>("start_backend");
      this.retryCount = 0;
      return result;
    } catch (error) {
      if (this.retryCount < this.maxRetries) {
        this.retryCount++;
        console.warn(`Backend start failed, retrying (attempt ${this.retryCount})...`);
        await this.delay(1000 * this.retryCount); // Exponential backoff
        return this.invokeStartBackend();
      }
      throw error;
    }
  }

  /**
   * Stop the Python backend process
   * Gracefully shuts down the subprocess
   */
  async stopBackend(): Promise<void> {
    try {
      await invoke<void>("stop_backend");
      console.log("Backend stopped successfully");
    } catch (error) {
      console.error("Failed to stop backend:", error);
      throw error;
    }
  }

  /**
   * Query the current backend status
   * Checks if the backend is running and healthy
   */
  async queryStatus(port: number = 8787): Promise<HealthStatus> {
    try {
      const result = await invoke<HealthStatus>("query_backend_status", { port });
      return result;
    } catch (error) {
      console.error("Failed to query backend status:", error);
      throw error;
    }
  }

  /**
   * Check if the backend is healthy (quick health check)
   */
  async isHealthy(port: number = 8787): Promise<boolean> {
    try {
      const status = await this.queryStatus(port);
      return status.reachable;
    } catch {
      return false;
    }
  }

  /**
   * Load application settings from persistent storage
   */
  async loadSettings(): Promise<AppSettings> {
    try {
      const result = await invoke<AppSettings>("get_settings");
      return result;
    } catch (error) {
      console.error("Failed to load settings:", error);
      throw error;
    }
  }

  /**
   * Save application settings to persistent storage
   */
  async saveSettings(settings: AppSettings): Promise<void> {
    try {
      await invoke<void>("save_settings", { settings });
      console.log("Settings saved successfully");
    } catch (error) {
      console.error("Failed to save settings:", error);
      throw error;
    }
  }

  /**
   * Make a proxied HTTP request to the backend
   * Automatically handles routing to localhost:8787
   */
  async proxyRequest(
    path: string,
    method: "GET" | "POST" | "PUT" | "DELETE" = "GET",
    body?: unknown
  ): Promise<string> {
    try {
      const result = await invoke<string>("proxy_backend_request", {
        path,
        method,
        body: body ? JSON.stringify(body) : undefined,
      });
      return result;
    } catch (error) {
      console.error("Backend proxy request failed:", error);
      throw error;
    }
  }

  /**
   * Utility: delay for retry logic
   */
  private delay(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }
}

/**
 * React hook for backend service (use with React Context)
 */
export function useBackend(): BackendService {
  return BackendService.getInstance();
}

/**
 * Detect setup configuration and provide diagnostics
 */
export async function detectSetup(): Promise<{
  lmStudioFound: boolean;
  nemoDbFound: boolean;
  diskSpaceOk: boolean;
  permissionsOk: boolean;
  ready: boolean;
  status: SetupStatus;
  issues: string[];
}> {
  try {
    // Route through the Tauri backend proxy to avoid browser CORS restrictions.
    const payload = await invoke<string>("proxy_backend_request", {
      path: "/api/startup",
      method: "GET",
    });
    const data = JSON.parse(payload);

    const lmStudioFound = Boolean(data.checks.lm_studio_reachable);
    const nemoDbFound = Boolean(data.checks.nemo_database_exists);
    const diskSpaceOk = Boolean(data.checks.disk_space_sufficient);
    const permissionsOk = Boolean(data.checks.permissions_ok);
    const ready = Boolean(data.ready);
    const issues = [];

    if (!lmStudioFound) issues.push("LM Studio not reachable");
    if (!nemoDbFound) issues.push("NEMO database missing");
    if (!diskSpaceOk) issues.push("Insufficient disk space");
    if (!permissionsOk) issues.push("Permission denied");

    const status: SetupStatus = ready
      ? "ready"
      : !lmStudioFound || !nemoDbFound || !permissionsOk
        ? "setup_required"
        : !diskSpaceOk
          ? "degraded"
          : "fatal_error";
    
    return {
      lmStudioFound,
      nemoDbFound,
      diskSpaceOk,
      permissionsOk,
      ready,
      status,
      issues,
    };
  } catch (error) {
    console.error("Failed to detect setup:", error);
    return {
      lmStudioFound: false,
      nemoDbFound: false,
      diskSpaceOk: false,
      permissionsOk: false,
      ready: false,
      status: "fatal_error",
      issues: ["Unable to reach startup probe"],
    };
  }
}
