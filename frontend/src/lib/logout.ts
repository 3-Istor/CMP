export async function logout() {
  try {
    const { clearTokenCache } = await import("@/lib/api");
    clearTokenCache();

    const response = await fetch("/api/auth/federated-logout", {
      method: "POST",
    });

    if (response.ok) {
      const data = await response.json();
      window.location.href = data.logoutUrl;
    } else {
      console.error("Logout failed");
      window.location.href = "/";
    }
  } catch (error) {
    console.error("Logout error:", error);
    window.location.href = "/";
  }
}
