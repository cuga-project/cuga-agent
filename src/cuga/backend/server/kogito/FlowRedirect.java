package org.cuga;

import org.jbpm.workflow.instance.NodeInstance;
import org.jbpm.workflow.instance.impl.WorkflowProcessInstanceImpl;
import org.kie.kogito.internal.process.runtime.KogitoProcessContext;

/**
 * In-process redirect of a running instance to an arbitrary node.
 *
 * <p>The Kogito counterpart to Flowable's
 * {@code RuntimeService.createChangeActivityStateBuilder().moveActivityIdTo(...)}, which
 * CUGA FLO's {@code Task_DynamicSkip} uses to realise a hook's SKIP_TO / TERMINATE. It has
 * to run in-process: over REST it would read committed state and race the still-executing
 * task that asked for the redirect.
 *
 * <p>Unlike the Flowable version this needs <em>no</em> boundary error event and no thrown
 * {@code BpmnError} — the calling script task performs the jump directly. That matters,
 * because Kogito rejects boundary events on script tasks at build time ("Boundary events
 * are supported only on StateBasedNode, found node: ActionNode"), so Flowable's
 * scriptTask + boundaryEvent + handler shape cannot be ported literally.
 *
 * <p>Verified against Kogito 10.2.0 by {@code redirectspike.bpmn}.
 */
public final class FlowRedirect {

    private FlowRedirect() {}

    /**
     * Cancel the calling node and continue execution at {@code targetNodeId}.
     *
     * <p>A blank {@code targetNodeId} is a no-op, so a hook that decided CONTINUE can call
     * this unconditionally and let the normal outgoing flow run.
     *
     * @param kcontext     script-task context of the node requesting the redirect
     * @param targetNodeId BPMN element id to jump to
     */
    public static void to(KogitoProcessContext kcontext, String targetNodeId) {
        if (targetNodeId == null || targetNodeId.isBlank()) {
            return; // CONTINUE — leave the nominal path alone
        }

        WorkflowProcessInstanceImpl pi =
                (WorkflowProcessInstanceImpl) kcontext.getProcessInstance();
        org.kie.api.definition.process.Node target = findNode(pi, targetNodeId);

        // Cancel the calling node first. Without this its own outgoing flow fires too and
        // the process runs both paths — the spike's original "A,D,B,D" trail.
        ((NodeInstance) kcontext.getNodeInstance()).cancel();

        pi.getNodeInstance(target).trigger(null, org.jbpm.workflow.core.Node.CONNECTION_DEFAULT_TYPE);
    }

    /**
     * Resolve a BPMN element id to its Node.
     *
     * <p>Matches on the {@code UniqueId} metadata, which holds the BPMN element id — note
     * {@code NodeContainer.getNodeByUniqueId(String)} looks promising but throws NPE here,
     * so the scan is deliberate rather than lazy.
     */
    private static org.kie.api.definition.process.Node findNode(
            WorkflowProcessInstanceImpl pi, String nodeId) {
        StringBuilder seen = new StringBuilder();
        for (org.kie.api.definition.process.Node n : pi.getNodeContainer().getNodes()) {
            Object uniqueId = n.getMetaData().get("UniqueId");
            if (nodeId.equals(uniqueId)) {
                return n;
            }
            seen.append(uniqueId).append(' ');
        }
        throw new IllegalStateException(
                "FlowRedirect: no node '" + nodeId + "' in process; saw: " + seen);
    }
}
