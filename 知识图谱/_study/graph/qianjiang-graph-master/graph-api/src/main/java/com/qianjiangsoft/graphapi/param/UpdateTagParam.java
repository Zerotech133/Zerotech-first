package com.qianjiangsoft.graphapi.param;

import com.qianjiangsoft.graphcore.model.PropertyInfo;
import lombok.Data;

import java.util.List;

/**
 * @author ulmarr8861
 * @date 2022/3/29
 */
@Data
public class UpdateTagParam {
    private String id;
    private String name;
    List<PropertyInfo> propertyInfoList;
    private String iconPath;

}
