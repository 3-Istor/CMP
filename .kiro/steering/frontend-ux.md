---
inclusion: always
---

# Frontend UX & UI Specifications (Phase 4)

We are building a modern Internal Developer Portal (IDP). The frontend is built with Next.js 15, Tailwind CSS, and Shadcn UI.
The UI must elegantly support our Multi-Provider architecture: "Legacy IaaS" (AWS/OpenStack) and "Modern GitOps" (Kubernetes/ArgoCD).

## 1. Global UI Principles

- **Aesthetic:** Clean, modern, "Vercel-like" interface. Use generous whitespace, subtle borders, and muted backgrounds for secondary sections.
- **Components:** Strictly use Shadcn UI components (`Card`, `Badge`, `Button`, `Dialog`, `Tabs`, `Switch`, `Slider`).
- **Icons:** Use `lucide-react`. Every action button should have a corresponding icon.
- **Feedback:** Always show loading states (skeletons or spinners) and use `sonner` for toast notifications on success/failure.

## 2. Navigation & Authentication Flow

- **Brand:** The platform is **CNP — Cloud Native Platform**. The shared logo lives in `src/components/brand/Logo.tsx` and the document title/metadata is set in `src/app/layout.tsx`.
- **Route protection (`src/middleware.ts`):**
  - `/auth/*` (Keycloak redirect dance) is always reachable.
  - `/welcome` is the public landing page; authenticated users hitting it are redirected to `/` (the app).
  - Every other route is protected. Unauthenticated users are sent to `/welcome?callbackUrl=<original-path>` so they return to their destination after logging in.
- **App shell (`src/components/layout/AppShell.tsx`):** The persistent sidebar is hidden on `/auth/*`, on `/welcome`, and whenever the session is not authenticated.
- **Persistent sidebar (`src/components/layout/AppSidebar.tsx`):** Lists the user's projects and deployments. It polls so the list reflects changes made elsewhere (projects every 15s; deployments 15s active / 30s idle via `useDeploymentsList`).

## 3. Page Specifications

### A. Public Landing Page (`/welcome`)

- **Audience:** Visitors who are not signed in yet (the entry point for the protected app).
- **Content:** Marketing homepage — hero with headline + stats, a Services grid, a Benefits section, and a final CTA, all built with the brand `Logo` and `lucide-react` icons.
- **Login:** Every CTA links to `/auth/signin?callbackUrl=<callbackUrl>`, preserving the `callbackUrl` query param forwarded by the middleware so the user lands on their intended page after Keycloak sign-in.

### B. User Account Page (`/account`)

- **New Feature:** Add a "Link GitHub Account" section.
- **UI:** A distinct `Card` component.
  - If the user is not linked (i.e., `github_installation_id` is null in their profile): Show a prominent "Connect GitHub" button that redirects to the GitHub App installation URL.
  - If linked: Show a green `Badge` "Connected" and display the `installation_id`.

### C. Catalog View (`/`)

- **Restructuring:** The catalog must visually separate Legacy templates from Modern Kubernetes templates.
- **UI:** Use Shadcn `Tabs` (`defaultValue="kubernetes"`).
  - Tab "Kubernetes & GitOps": Displays templates where `provider_type == KUBERNETES`.
  - Tab "IaaS & VMs": Displays templates where `provider_type == LEGACY_HYBRID`.
- **Deploy Modal:** When a user clicks "Deploy" on a KUBERNETES template, the form must verify that the user has linked their GitHub account. If not, show an `Alert` advising them to go to the Account page.

### D. Project Dashboard (New View - e.g., `/projects/[id]`)

_Note: A Project represents a Team boundary (tied to Keycloak Groups)._

- **Header:** Project Name, creation date, and total number of applications.
- **Aggregated Health Ring:** A visual summary (e.g., 4 Healthy, 1 Degraded apps).
- **Application Grid:** A list of `Card` components for each app in the project.
  - **Tile Info:** App Name, Tech Stack Icon, Live Status Badge, and Environment.
  - **Filtering:** Add small toggle buttons (or a Select) to filter tiles by: Healthy, Degraded, Internet Exposed.
- **Members Panel:** A side panel or bottom section listing project members (fetched via Keycloak API) with their roles (Admin/Member).

### E. Application Dashboard (The "Control Center")

This is the detail view for a specific application.

- **Header:** Status Badge (e.g., RUNNING), Environment (Production/Staging), Last Deploy Time, and Public URL.
- **Quick Action Panel (Grid of buttons):**
  - `[ 🐙 Open GitHub Repository ]` (Opens `github_repo_url`)
  - `[ 🔒 Manage Secrets in Vault ]` (Deep link to Vault KV path)
  - `[ 🦑 View in ArgoCD ]` (Deep link to ArgoCD App)
  - `[ 🗑️ Delete App ]` (Requires typing the app name to confirm)
- **Infrastructure Toggles (GitOps Day-2 Operations):**
  - Rendered inside a `Card` titled "Infrastructure Configuration".
  - **Replicas:** A Shadcn `Slider` (1 to 10).
  - **Internet Exposure:** A Shadcn `Switch`.
  - **SSO Protection:** A Shadcn `Switch`.
  - _Interaction:_ Changing any of these shows a "Save Changes" button. Clicking it triggers the backend API to commit the changes to Git.
- **Deployment History Table:**
  - A Shadcn `Table` showing previous deployments (Commit Hash, Branch, Author, Duration, Status).
