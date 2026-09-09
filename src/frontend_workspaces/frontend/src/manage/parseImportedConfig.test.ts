import { describe, it, expect } from "vitest";
import { parseImportedSupervisorFields } from "./parseImportedConfig";

describe("parseImportedSupervisorFields", () => {
  it("round-trips supervisor kind, subAgents, and planApproval", () => {
    const exported = {
      agent: { name: "Trip Supervisor", description: "Delegates travel tasks", kind: "supervisor" },
      supervisor: {
        subAgents: [{ kind: "internal", ref: "crm-agent" }],
        planApproval: true,
      },
    };

    expect(parseImportedSupervisorFields(exported)).toEqual({
      agentName: "Trip Supervisor",
      agentDescription: "Delegates travel tasks",
      agentKind: "supervisor",
      subAgents: [{ kind: "internal", ref: "crm-agent" }],
      planApproval: true,
    });
  });

  it("preserves name and description when supervisor fields are absent", () => {
    expect(
      parseImportedSupervisorFields({
        agent: { name: "Flight Booker", description: "Books flights" },
      }),
    ).toEqual({
      agentName: "Flight Booker",
      agentDescription: "Books flights",
    });
  });

  it("drops malformed sub-agents and ignores non-boolean planApproval", () => {
    expect(
      parseImportedSupervisorFields({
        supervisor: {
          subAgents: [null, { kind: "internal" }, { kind: "internal", ref: "crm-agent" }, "x"],
          planApproval: "false",
        },
      }),
    ).toEqual({
      subAgents: [{ kind: "internal", ref: "crm-agent" }],
    });
  });
});
