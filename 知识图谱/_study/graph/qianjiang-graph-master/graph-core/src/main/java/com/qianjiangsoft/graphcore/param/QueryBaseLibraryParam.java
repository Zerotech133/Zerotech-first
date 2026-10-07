package com.qianjiangsoft.graphcore.param;

import lombok.Data;
import lombok.experimental.Accessors;

/**
 * 基础-分组信息对象 base_table_group
 * 
 * @author qianjiang
 * @date 2021-03-25
 */
@Data
@Accessors(chain = true)
public class QueryBaseLibraryParam {
	private static final long serialVersionUID = 1L;

	private String name;

	private String groupId;

	private String type;

	private String caseId;

	private Long tableType;

	private String tableSource;

}
