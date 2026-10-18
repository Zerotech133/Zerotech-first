from zhice_os_v2.domain.ontology.actions import Action, ActionStatus
from zhice_os_v2.domain.ontology.objects import ObjectRef, OntologyObject, Provenance


def test_ontology_object_has_ref_and_provenance():
    obj = OntologyObject(object_id="strat-1", object_type="Strategy")
    obj.set_provenance(Provenance(source_system="research_gateway"))
    assert obj.ref().object_type == "Strategy"
    assert obj.provenance is not None
    assert obj.provenance.source_system == "research_gateway"


def test_action_lifecycle_approve():
    action = Action(
        action_id="act-1",
        action_type="approve_trade",
        target=ObjectRef(object_id="apr-1", object_type="ApprovalRequest"),
    )
    action.submit(actor="system")
    assert action.status == ActionStatus.PENDING_APPROVAL
    action.approve(actor="user")
    assert action.status == ActionStatus.EXECUTING
    assert action.approved_by == "user"
    assert len(action.audit_trail) == 2


def test_action_reject_requires_pending():
    action = Action(
        action_id="act-2",
        action_type="approve_trade",
        target=ObjectRef(object_id="apr-2", object_type="ApprovalRequest"),
    )
    action.submit(actor="system")
    action.reject(actor="user", reason="risk too high")
    assert action.status == ActionStatus.REJECTED
    assert action.audit_trail[-1].detail["reason"] == "risk too high"
