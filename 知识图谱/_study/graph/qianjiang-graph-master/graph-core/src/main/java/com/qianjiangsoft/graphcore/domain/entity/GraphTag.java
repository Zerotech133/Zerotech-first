package com.qianjiangsoft.graphcore.domain.entity;

import com.baomidou.mybatisplus.annotation.TableField;
import com.baomidou.mybatisplus.annotation.TableId;
import com.baomidou.mybatisplus.annotation.TableName;
import com.baomidou.mybatisplus.extension.handlers.JacksonTypeHandler;
import com.qianjiangsoft.graphcore.model.PropertyInfo;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

import java.util.List;

@Data
@AllArgsConstructor
@NoArgsConstructor
@TableName(autoResultMap = true)
public class GraphTag {

    @TableId
    private String id;

    private String name;

    @TableField(typeHandler = JacksonTypeHandler.class)
    private List<PropertyInfo> propertyInfoList;

    String spaceId;

    String iconPath;
}