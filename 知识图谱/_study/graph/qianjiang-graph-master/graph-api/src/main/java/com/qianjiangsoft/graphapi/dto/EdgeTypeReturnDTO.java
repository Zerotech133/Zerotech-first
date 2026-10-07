package com.qianjiangsoft.graphapi.dto;

import com.qianjiangsoft.graphcore.model.PropertyInfo;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/4/1
 */
@Data
public class EdgeTypeReturnDTO {
    private String id;

    private String name;

    private List<PropertyInfo> propertyInfoList;
}