package com.qianjiangsoft.graphcore.model;

import com.qianjiangsoft.graphcore.constants.PropertyType;
import lombok.AllArgsConstructor;
import lombok.Data;
import lombok.NoArgsConstructor;

/**
 * @author marshallwu
 * @date 2022/2/23 16:30
 */
@Data
@AllArgsConstructor
@NoArgsConstructor
public class PropertyInfo {

    String key;

    PropertyType propertyType;

    boolean nullable = true;

    public PropertyInfo(String key, PropertyType propertyType) {
        this.key = key;
        this.propertyType = propertyType;
    }

}
