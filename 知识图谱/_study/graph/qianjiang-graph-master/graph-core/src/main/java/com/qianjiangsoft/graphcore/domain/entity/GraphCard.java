package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.IdType;
import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import lombok.Data;

import java.util.List;

@Data
@TableName(autoResultMap = true)
public class GraphCard {

    @TableId(type = IdType.AUTO)
    private Integer id;

    private String name;

    @TableField("`key`")
    private String key;

    private String spaceId;

    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<GraphCardOption> optionList;
}