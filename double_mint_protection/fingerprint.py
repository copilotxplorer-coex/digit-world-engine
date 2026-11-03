def build_fingerprint(operation_id, checkpoint_id, authority_version):
    return f"{operation_id}:{checkpoint_id}:{authority_version}"
