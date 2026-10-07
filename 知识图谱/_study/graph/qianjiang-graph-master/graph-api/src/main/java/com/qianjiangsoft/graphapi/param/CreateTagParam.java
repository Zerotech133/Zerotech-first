package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.PropertyInfo;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/18
 */
@Data
public class CreateTagParam {
    private String id;
    private String name;
    private List<PropertyInfo> propertyInfoList;
    private String iconPath;
}
