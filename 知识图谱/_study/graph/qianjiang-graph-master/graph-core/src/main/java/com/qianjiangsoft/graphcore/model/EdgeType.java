package com.qianjiangsoft.graphcore.model;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.List;
import java.util.Objects;

/**
 * @author marshallwu
 * @date 2022/2/23 16:35
 */
@Data
@AllArgsConstructor
public class EdgeType {

    String id;

    String name;
    
    List<PropertyInfo> propertyInfoList;

    @Override
    public boolean equals(Object o) {
        if (this == o) {
            return true;
        }
        if (o == null || getClass() != o.getClass()) {
            return false;
        }
        EdgeType edgeType = (EdgeType) o;
        return Objects.equals(getName(), edgeType.getName());
    }

    @Override
    public int hashCode() {
        return Objects.hash(getName());
    }
}
