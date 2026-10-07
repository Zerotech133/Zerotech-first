package com.qianjiangsoft.graphcore.model;

import lombok.AllArgsConstructor;
import lombok.Data;

import java.util.List;
import java.util.Objects;

/**
 * @author marshallwu
 * @date 2022/2/23 16:34
 */
@Data
@AllArgsConstructor
public class Tag {

    String id;

    String name;

    List<PropertyInfo> propertyInfoList;

    String spaceId;

    String iconPath;

    @Override
    public boolean equals(Object o) {
        if (this == o) return true;
        if (o == null || getClass() != o.getClass()) return false;
        Tag tag = (Tag) o;
        return getName().equals(tag.getName());
    }

    @Override
    public int hashCode() {
        return Objects.hash(getName());
    }
}
