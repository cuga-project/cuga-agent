import React from "react";
import { Button, Stack, Tile, Heading } from "@carbon/react";
import { Locked, ArrowLeft, Home } from "@carbon/icons-react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "./AuthContext";

export function UnauthorizedPage() {
  const navigate = useNavigate();
  const { user } = useAuth();

  return (
    <div style={{ 
      display: 'flex', 
      alignItems: 'center', 
      justifyContent: 'center', 
      minHeight: '100vh',
      padding: '2rem'
    }}>
      <Tile style={{ maxWidth: '600px', width: '100%' }}>
        <Stack gap={6}>
          <div style={{ 
            display: 'flex', 
            justifyContent: 'center',
            marginBottom: '1rem'
          }}>
            <div style={{
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: '120px',
              height: '120px',
              background: 'var(--cds-layer-02)',
              borderRadius: '50%',
              color: 'var(--cds-text-error)'
            }}>
              <Locked size={64} />
            </div>
          </div>

          <Heading style={{ textAlign: 'center' }}>Access Denied</Heading>
          
          <p style={{ 
            textAlign: 'center',
            color: 'var(--cds-text-secondary)',
            fontSize: '1.125rem'
          }}>
            You don't have permission to access this page.
          </p>
          
          {user && (
            <Tile style={{ 
              background: 'var(--cds-layer-02)',
              borderLeft: '4px solid var(--cds-border-interactive)'
            }}>
              <Stack gap={3}>
                <p style={{ 
                  fontSize: '0.875rem',
                  color: 'var(--cds-text-secondary)',
                  margin: 0
                }}>
                  Signed in as: <strong style={{ 
                    color: 'var(--cds-text-primary)',
                    fontWeight: 600
                  }}>{user.email || user.name || user.sub}</strong>
                </p>
                {user.roles && user.roles.length > 0 && (
                  <p style={{ 
                    fontSize: '0.875rem',
                    color: 'var(--cds-text-secondary)',
                    margin: 0
                  }}>
                    Your roles: <strong style={{ 
                      color: 'var(--cds-text-primary)',
                      fontWeight: 600
                    }}>{user.roles.join(", ")}</strong>
                  </p>
                )}
              </Stack>
            </Tile>
          )}
          
          <p style={{ 
            textAlign: 'center',
            fontSize: '0.875rem',
            color: 'var(--cds-text-secondary)'
          }}>
            If you believe you should have access to this page, please contact your administrator.
          </p>
          
          <Stack gap={4} orientation="horizontal" style={{ justifyContent: 'center' }}>
            <Button
              kind="secondary"
              renderIcon={ArrowLeft}
              onClick={() => navigate(-1)}
            >
              Go Back
            </Button>
            <Button
              kind="primary"
              renderIcon={Home}
              onClick={() => navigate("/chat")}
            >
              Go to Chat
            </Button>
          </Stack>
        </Stack>
      </Tile>
    </div>
  );
}

// Made with Bob
