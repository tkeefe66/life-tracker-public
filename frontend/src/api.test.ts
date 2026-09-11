import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { apiGet, login, LockedOutError, logout, logoutAll, onUnauthorized, UnauthorizedError } from "./api";

describe("login", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("resolves true on a successful login", async () => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
    await expect(login("correct")).resolves.toBe(true);
  });

  it("resolves false on a wrong password (401), distinct from a lockout", async () => {
    fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);
    await expect(login("wrong")).resolves.toBe(false);
  });

  it("throws LockedOutError on 429 instead of resolving false", async () => {
    // A 429 previously read as resp.ok === false, indistinguishable from a
    // wrong password — the owner would keep retrying and extend their own
    // lockout. It must be surfaced distinctly.
    fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 429 });
    vi.stubGlobal("fetch", fetchMock);
    await expect(login("anything")).rejects.toBeInstanceOf(LockedOutError);
  });
});

describe("logout", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs to /api/logout so the server invalidates the session", async () => {
    await logout();
    expect(fetchMock).toHaveBeenCalledWith("/api/logout", { method: "POST" });
  });

  it("never throws even if the request fails — the client always treats logout as done", async () => {
    fetchMock.mockRejectedValue(new Error("network down"));
    await expect(logout()).resolves.toBeUndefined();
  });
});

describe("logoutAll", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: true, status: 200 });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("POSTs to /api/logout-all so every session is revoked", async () => {
    await logoutAll();
    expect(fetchMock).toHaveBeenCalledWith("/api/logout-all", { method: "POST" });
  });

  it("never throws even if the request fails — the client always treats sign-out as done", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network"));
    await expect(logoutAll()).resolves.toBeUndefined();
  });
});

describe("onUnauthorized", () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    fetchMock = vi.fn().mockResolvedValue({ ok: false, status: 401 });
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    onUnauthorized(() => {});
    vi.unstubAllGlobals();
  });

  it("fires the registered handler on any 401, not just the initial probe", async () => {
    const handler = vi.fn();
    onUnauthorized(handler);
    await expect(apiGet("/targets")).rejects.toBeInstanceOf(UnauthorizedError);
    expect(handler).toHaveBeenCalledTimes(1);
  });
});
