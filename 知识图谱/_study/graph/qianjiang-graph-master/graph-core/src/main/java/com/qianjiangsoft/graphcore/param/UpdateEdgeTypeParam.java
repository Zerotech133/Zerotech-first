package com.qianjiangsoft.graphcore.param;

import com.qianjiangsoft.graphcore.model.PropertyInfo;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/31
 */
@Data
public class UpdateEdgeTypeParam {
    private String id;

    private String name;

    private List<PropertyInfo> propertyInfoList;
}
