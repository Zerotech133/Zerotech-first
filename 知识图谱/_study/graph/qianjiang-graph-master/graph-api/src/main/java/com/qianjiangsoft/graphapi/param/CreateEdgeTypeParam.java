package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.PropertyInfo;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/30
 */
@Data
public class CreateEdgeTypeParam {
    private String name;

    private List<PropertyInfo> propertyInfoList;
}
