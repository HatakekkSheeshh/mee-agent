---
config:
  flowchart:
    curve: linear
---
graph TD;
	__start__([__start__]):::first
	check_auth(check_auth)
	classify_request(classify_request)
	need_more_info(need_more_info)
	summarize(summarize)
	check_info(check_info)
	check_issue_id(check_issue_id)
	check_project_metadata(check_project_metadata)
	format_update_request(format_update_request)
	issue_approve(issue_approve)
	edit_issues(edit_issues)
	call_model(call_model)
	check_list_issues_info(check_list_issues_info)
	get_list_issues(get_list_issues)
	get_projects(get_projects)
	get_prompts(get_prompts)
	get_issue_summary_check_info(get_issue_summary_check_info)
	get_issue_summary(get_issue_summary)
	task_management_check_info(task_management_check_info)
	task_management_check_metadata(task_management_check_metadata)
	task_management(task_management)
	batch_check_info(batch_check_info)
	batch_check_issue_id(batch_check_issue_id)
	batch_check_metadata(batch_check_metadata)
	batch_get_children(batch_get_children)
	batch_update_issues(batch_update_issues)
	bulk_update_check_info(bulk_update_check_info)
	bulk_update_get_issues(bulk_update_get_issues)
	bulk_update_check_metadata(bulk_update_check_metadata)
	bulk_update_execute(bulk_update_execute)
	reconcile_check_info(reconcile_check_info)
	reconcile_fetch_candidates(reconcile_fetch_candidates)
	reconcile_match(reconcile_match)
	gitlab_event_handler(gitlab_event_handler)
	tools_node(tools_node):::orphan
	__end__([__end__]):::last

	__start__ --> check_auth;
	check_auth -- auth_required --> need_more_info;
	check_auth --> classify_request;

	classify_request -- summarize --> summarize;
	classify_request -- update --> check_info;
	classify_request -- get_issues --> check_list_issues_info;
	classify_request -- get_projects --> get_projects;
	classify_request -- get_prompts --> get_prompts;
	classify_request -- get_issue_summary --> get_issue_summary_check_info;
	classify_request -- batch_update --> batch_check_info;
	classify_request -- bulk_update --> bulk_update_check_info;
	classify_request -- task_management --> task_management_check_info;
	classify_request -- reconcile --> reconcile_check_info;
	classify_request -- gitlab_event --> gitlab_event_handler;
	classify_request -- not_verified --> need_more_info;

	summarize -- need_more_info --> need_more_info;
	summarize -- update_issues --> reconcile_check_info;
	summarize --> __end__;

	check_info -- need_more_info --> need_more_info;
	check_info --> check_issue_id;
	check_issue_id -- need_more_info --> need_more_info;
	check_issue_id --> check_project_metadata;
	check_project_metadata -- need_more_info --> need_more_info;
	check_project_metadata --> format_update_request;

	format_update_request -- need_more_info --> need_more_info;
	format_update_request -- get_issues --> get_list_issues;
	format_update_request -- update --> issue_approve;
	format_update_request --> __end__;

	issue_approve -- approve --> call_model;
	issue_approve -- edit --> edit_issues;
	issue_approve -- reject --> __end__;
	edit_issues --> issue_approve;
	edit_issues -- need_more_info --> need_more_info;
	call_model --> __end__;

	check_list_issues_info -- need_more_info --> need_more_info;
	check_list_issues_info --> get_list_issues;
	get_list_issues --> __end__;
	get_projects --> __end__;
	get_prompts --> __end__;

	get_issue_summary_check_info -- need_more_info --> need_more_info;
	get_issue_summary_check_info --> get_issue_summary;
	get_issue_summary --> __end__;

	task_management_check_info -- need_more_info --> need_more_info;
	task_management_check_info --> task_management_check_metadata;
	task_management_check_metadata -- need_more_info --> need_more_info;
	task_management_check_metadata --> task_management;
	task_management --> __end__;

	batch_check_info -- need_more_info --> need_more_info;
	batch_check_info --> batch_check_issue_id;
	batch_check_issue_id -- need_more_info --> need_more_info;
	batch_check_issue_id --> batch_check_metadata;
	batch_check_metadata -- need_more_info --> need_more_info;
	batch_check_metadata --> batch_get_children;
	batch_get_children --> batch_update_issues;
	batch_update_issues --> __end__;

	bulk_update_check_info -- need_more_info --> need_more_info;
	bulk_update_check_info --> bulk_update_get_issues;
	bulk_update_get_issues --> bulk_update_check_metadata;
	bulk_update_check_metadata -- need_more_info --> need_more_info;
	bulk_update_check_metadata --> bulk_update_execute;
	bulk_update_execute --> __end__;

	reconcile_check_info -- need_more_info --> need_more_info;
	reconcile_check_info --> reconcile_fetch_candidates;
	reconcile_fetch_candidates --> reconcile_match;
	reconcile_match -- need_more_info --> need_more_info;
	reconcile_match --> format_update_request;

	gitlab_event_handler --> __end__;

	need_more_info -. "resume: submit (back to last_node)" .-> classify_request;
	need_more_info -. cancel .-> __end__;

	classDef default fill:#f2f0ff,line-height:1.2
	classDef first fill-opacity:0
	classDef last fill:#bfb6fc
	classDef orphan fill:#ffd9d9,stroke-dasharray:5 5