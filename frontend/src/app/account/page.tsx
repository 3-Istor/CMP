"use client";

import { ArrowLeft, Camera, Loader2, Upload } from "lucide-react";
import { useTranslations } from "next-intl";
import { useSession } from "next-auth/react";
import Image from "next/image";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { toast } from "sonner";

import { GitHubLinkButton } from "@/components/account/GitHubLinkButton";
import { LanguageSwitcher } from "@/components/layout/LanguageSwitcher";
import { ThemeCard } from "@/components/account/ThemeCard";
import { ConnectedAssistants } from "@/components/agents/ConnectedAssistants";
import { Avatar } from "@/components/ui/avatar";
import { Button } from "@/components/ui/button";
import {
    Card,
    CardContent,
    CardDescription,
    CardHeader,
    CardTitle,
} from "@/components/ui/card";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { getCurrentUser, uploadProfilePicture } from "@/lib/api";
import type { UserProfile } from "@/types";

const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/gif", "image/webp"];
const MAX_SIZE_MB = 5;
const MAX_SIZE_BYTES = MAX_SIZE_MB * 1024 * 1024;

type Translate = (key: string, values?: Record<string, string | number>) => string;

function validateImageFile(file: File, t: Translate): string | null {
  if (!ALLOWED_TYPES.includes(file.type)) {
    return t("page.invalidType");
  }
  if (file.size > MAX_SIZE_BYTES) {
    return t("page.tooLarge", {
      size: (file.size / 1024 / 1024).toFixed(1),
      max: MAX_SIZE_MB,
    });
  }
  return null;
}

export default function AccountPage() {
  const t = useTranslations("account");
  const [user, setUser] = useState<UserProfile | null>(null);
  const [loading, setLoading] = useState(true);
  const [uploading, setUploading] = useState(false);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const router = useRouter();
  const { update } = useSession();
  const tLanguage = useTranslations("account.language");

  const fetchUser = async () => {
    try {
      const data = await getCurrentUser();
      setUser(data);
    } catch (error) {
      toast.error(
        t("page.loadFailedToast", {
          error: error instanceof Error ? error.message : t("page.unknownError"),
        }),
      );
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUser();
  }, []);

  const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;

    const error = validateImageFile(file, t);
    if (error) {
      toast.error(error);
      e.target.value = "";
      return;
    }

    setSelectedFile(file);
    const reader = new FileReader();
    reader.onloadend = () => setPreviewUrl(reader.result as string);
    reader.readAsDataURL(file);
  };

  const handleUpload = async () => {
    if (!selectedFile) {
      toast.error(t("page.selectFirst"));
      return;
    }

    setUploading(true);
    try {
      const response = await uploadProfilePicture(selectedFile);
      toast.success(response.message);
      setSelectedFile(null);
      setPreviewUrl(null);
      if (fileInputRef.current) {
        fileInputRef.current.value = "";
      }
      // Refresh user data to show new avatar
      setUser((prev) =>
        prev ? { ...prev, picture: response.picture_url } : null,
      );

      // Refresh NextAuth session to update avatar in the sidebar
      await update();

      // Also fetch fresh user data to ensure consistency
      await fetchUser();
    } catch (error) {
      toast.error(
        t("page.uploadFailed", {
          error: error instanceof Error ? error.message : t("page.unknownError"),
        }),
      );
    } finally {
      setUploading(false);
    }
  };

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();

    const file = e.dataTransfer.files?.[0];
    if (!file) return;

    const error = validateImageFile(file, t);
    if (error) {
      toast.error(error);
      return;
    }

    setSelectedFile(file);
    const reader = new FileReader();
    reader.onloadend = () => setPreviewUrl(reader.result as string);
    reader.readAsDataURL(file);
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-background">
        <div className="container mx-auto max-w-4xl p-6">
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
            <span className="ml-3 text-muted-foreground">
              {t("page.loading")}
            </span>
          </div>
        </div>
      </div>
    );
  }

  if (!user) {
    return (
      <div className="min-h-screen bg-background">
        <div className="container mx-auto max-w-4xl p-6">
          <div className="flex items-center justify-center py-12">
            <p className="text-destructive">{t("page.loadFailed")}</p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background">
      {/* Header */}
      <header className="border-b">
        <div className="container mx-auto max-w-4xl px-6 py-4">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => router.push("/")}
            className="mb-2"
          >
            <ArrowLeft className="mr-2 h-4 w-4" />
            {t("page.back")}
          </Button>
          <h1 className="text-3xl font-bold">{t("page.title")}</h1>
          <p className="text-sm text-muted-foreground mt-1">
            {t("page.subtitle")}
          </p>
        </div>
      </header>

      <main className="container mx-auto max-w-4xl p-6">
        <div className="space-y-6">
          {/* Profile Card */}
          <Card>
            <CardHeader>
              <CardTitle>{t("profile.title")}</CardTitle>
              <CardDescription>{t("profile.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-6">
              {/* Avatar Section */}
              <div className="flex items-start gap-6">
                <Avatar
                  src={user.picture}
                  alt={user.name || user.email}
                  fallback={user.name || user.email}
                  className="h-24 w-24 text-2xl"
                />
                <div className="flex-1">
                  <h3 className="text-xl font-semibold">
                    {user.name ||
                      `${user.given_name || ""} ${user.family_name || ""}`.trim() ||
                      user.email}
                  </h3>
                  <p className="text-sm text-muted-foreground">{user.email}</p>
                  {user.groups.length > 0 && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      {user.groups.map((group) => (
                        <span
                          key={group}
                          className="inline-flex items-center rounded-full bg-primary/10 px-2.5 py-0.5 text-xs font-medium text-primary"
                        >
                          {group}
                        </span>
                      ))}
                    </div>
                  )}
                </div>
              </div>

              <Separator />

              {/* Details Grid */}
              <div className="grid gap-4 md:grid-cols-2">
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t("profile.firstName")}
                  </Label>
                  <p className="text-sm font-medium">
                    {user.given_name || "-"}
                  </p>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t("profile.lastName")}
                  </Label>
                  <p className="text-sm font-medium">
                    {user.family_name || "-"}
                  </p>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t("profile.email")}
                  </Label>
                  <p className="text-sm font-medium">{user.email}</p>
                </div>
                <div className="space-y-1">
                  <Label className="text-xs text-muted-foreground">
                    {t("profile.userId")}
                  </Label>
                  <p className="text-sm font-mono text-muted-foreground break-all">
                    {user.sub || "-"}
                  </p>
                </div>
              </div>
            </CardContent>
          </Card>

          {/* Avatar Upload Card */}
          <Card>
            <CardHeader>
              <CardTitle>{t("picture.title")}</CardTitle>
              <CardDescription>{t("picture.description")}</CardDescription>
            </CardHeader>
            <CardContent className="space-y-4">
              {/* Drag & Drop Zone */}
              <div
                onDragOver={handleDragOver}
                onDrop={handleDrop}
                className="relative flex flex-col items-center justify-center rounded-lg border-2 border-dashed border-muted-foreground/25 bg-muted/5 p-8 transition-colors hover:border-muted-foreground/50 hover:bg-muted/10"
              >
                <input
                  id="picture"
                  type="file"
                  accept="image/*"
                  ref={fileInputRef}
                  onChange={handleFileChange}
                  className="hidden"
                />

                {previewUrl ? (
                  <div className="flex flex-col items-center gap-4">
                    <div className="relative h-32 w-32 rounded-full overflow-hidden ring-2 ring-border">
                      <Image
                        src={previewUrl}
                        alt={t("picture.preview")}
                        fill
                        className="object-cover"
                      />
                    </div>
                    <p className="text-sm text-muted-foreground">
                      {selectedFile?.name}
                    </p>
                  </div>
                ) : (
                  <>
                    <div className="rounded-full bg-muted p-4 mb-4">
                      <Camera className="h-8 w-8 text-muted-foreground" />
                    </div>
                    <p className="text-sm font-medium mb-1">
                      {t("picture.dropTitle")}
                    </p>
                    <p className="text-xs text-muted-foreground mb-4">
                      {t("picture.dropOr")}
                    </p>
                  </>
                )}

                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={() => fileInputRef.current?.click()}
                  className="mt-2"
                >
                  <Upload className="mr-2 h-4 w-4" />
                  {t("picture.choose")}
                </Button>
              </div>

              {/* Upload Button */}
              {selectedFile && (
                <div className="flex items-center justify-between rounded-lg border bg-muted/50 p-4">
                  <div className="flex items-center gap-3">
                    <div className="rounded-full bg-primary/10 p-2">
                      <Camera className="h-4 w-4 text-primary" />
                    </div>
                    <div>
                      <p className="text-sm font-medium">{t("picture.ready")}</p>
                      <p className="text-xs text-muted-foreground">
                        {t("picture.fileSize", {
                          name: selectedFile.name,
                          size: (selectedFile.size / 1024).toFixed(1),
                        })}
                      </p>
                    </div>
                  </div>
                  <Button onClick={handleUpload} disabled={uploading} size="sm">
                    {uploading ? (
                      <>
                        <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                        {t("picture.uploading")}
                      </>
                    ) : (
                      <>
                        <Upload className="mr-2 h-4 w-4" />
                        {t("picture.upload")}
                      </>
                    )}
                  </Button>
                </div>
              )}

              <p className="text-xs text-muted-foreground">
                {t("picture.formats", { max: MAX_SIZE_MB })}
              </p>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle>{tLanguage("title")}</CardTitle>
              <CardDescription>{tLanguage("description")}</CardDescription>
            </CardHeader>
            <CardContent>
              <LanguageSwitcher />
            </CardContent>
          </Card>

          <ThemeCard />

          {/* GitHub Integration Card */}
          <GitHubLinkButton />

          <ConnectedAssistants />
        </div>
      </main>
    </div>
  );
}
