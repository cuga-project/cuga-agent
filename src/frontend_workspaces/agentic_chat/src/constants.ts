import { UserType } from "@carbon/ai-chat";

export const RESPONSE_USER_PROFILE = {
  id: "ai-chatbot-user",
  userName: "CUGA",
  fullName: "CUGA Agent",
  displayName: "CUGA",
  accountName: "CUGA Agent",
  replyToId: "ai-chatbot-user",
  userType: UserType.BOT,
};

export const getApiBaseUrl = (): string => {
  if (typeof window === 'undefined') return 'http://localhost:7860';
  const { hostname, protocol, origin, port } = window.location;
  if (hostname !== 'localhost' && hostname !== '127.0.0.1') return origin;
  if (port === '3002') return origin;
  return `${protocol}//${hostname}:7860`;
};

export const API_BASE_URL = getApiBaseUrl();
