package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import lombok.Data;
import lombok.ToString;
import lombok.experimental.Accessors;

import java.util.Date;

/**
 * 基础-分组信息对象 base_table_group
 *
 * @author qianjiang
 * @date 2021-03-25
 */
@Data
@ToString
@Accessors(chain = true)
public class BaseLibrary {
	private static final long serialVersionUID = 1L;

	@TableId
	private String id;

	private String name;

	private String groupId;

	private String spaceId;

	private Date createTime;

	@TableField(exist = false)
	private Long relationalGraphCount;
}
