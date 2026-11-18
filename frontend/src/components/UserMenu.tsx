"use client";

import React, { useEffect, useState } from "react";
import { useAuth } from "@/context/AuthContext";
import { ProfileDropdown, type Profile } from "@/components/ui/profile-dropdown";
import { apiFetch } from "@/lib/api";

export default function UserMenu() {
  const { user, logout } = useAuth();
  const [modelName, setModelName] = useState("GPT-4o");

  useEffect(() => {
    async function fetchAiModel() {
      try {
        const res = await apiFetch("/system/settings");
        if (res.ok) {
          const data = await res.json();
          if (data.ai?.model) {
            setModelName(data.ai.model);
          }
        }
      } catch {
        // graceful fallback to default
      }
    }
    void fetchAiModel();
  }, []);

  if (!user) return null;

  const email = user.email || "operator@trading.local";
  const rawName = email.split("@")[0];
  const formattedName = rawName.charAt(0).toUpperCase() + rawName.slice(1);

  const profileData: Profile = {
    name: formattedName,
    email: email,
    avatar: "", // Uses dynamic theme-matched quant initials badge
    subscription: user.is_platform_admin ? "PRO ADMIN" : user.tenant_id.toUpperCase(),
    model: modelName,
  };

  return (
    <ProfileDropdown
      data={profileData}
      onSignOut={() => void logout()}
    />
  );
}
