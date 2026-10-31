import React from "react";
import { Button, Stack, Tile, Heading, Tag } from "@carbon/react";
import { Locked, ArrowLeft, Home } from "@carbon/icons-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext";
import { CugaHeader } from "./CugaHeader";

export function UnauthorizedPage() {
  const navigate = useNavigate();
  const { user } = useAuth();

  return (
    <div className="unauthorized-page">
      <CugaHeader title="CUGA Agent" />
      <div role="main" id="main-content" tabIndex={-1} className="unauthorized-content">
        <Tile>
          <Stack gap={6}>
            <Locked size={32} aria-hidden="true" />
            <p className="unauthorized-code">403</p>
            <Heading>Access Denied</Heading>
            <p>You don&apos;t have permission to access this page.</p>
            {user && (
              <div className="unauthorized-identity">
                <p>Signed in as: <strong>{user.email || user.name || user.sub}</strong></p>
                {user.roles && user.roles.length > 0 && (
                  <div><p>Your roles:</p>{user.roles.map((role) => <Tag key={role} type="gray">{role}</Tag>)}</div>
                )}
              </div>
            )}
            <p>If you believe you should have access to this page, please contact your administrator.</p>
            <div className="unauthorized-actions">
              <Button kind="secondary" renderIcon={ArrowLeft} onClick={() => navigate(-1)}>Go Back</Button>
              <Button renderIcon={Home} onClick={() => navigate("/chat")}>Go to Chat</Button>
            </div>
          </Stack>
        </Tile>
      </div>
    </div>
  );
}
